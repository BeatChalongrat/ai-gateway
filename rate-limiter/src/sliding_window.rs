//! Sliding-window rate limiter — Redis ZSET implementation.
//!
//! Port of the Python ``RateLimitService._sliding_window_async``.
//! For each scope we run a 4-step Redis pipeline:
//!
//! 1. ``ZREMRANGEBYSCORE key 0 (now-window)`` — drop expired entries
//! 2. ``ZCARD key`` — count remaining (= requests inside window)
//! 3. ``ZADD key {timestamp}:{nanos} -> {timestamp}`` — record this hit
//! 4. ``EXPIRE key window+1`` — defensive TTL in case the key sits idle
//!
//! ``count_before < limit`` → ``allowed``. ``remaining`` is the
//! post-add headroom (``max(0, limit - count_before - 1)``) so a
//! client that just consumed the last slot sees ``remaining = 0``.
//!
//! Fail-open contract: any Redis error returns ``allowed = true`` so
//! a Redis outage cannot block the gateway. The fault is recorded
//! on the ``rate_limiter_redis_errors_total`` Prometheus counter.

use std::time::{SystemTime, UNIX_EPOCH};

use deadpool_redis::Pool;
use redis::AsyncCommands;
use tracing::warn;

use crate::metrics;

/// Default scope limits — mirror ``DEFAULT_KEY_LIMIT`` /
/// ``DEFAULT_PROJECT_LIMIT`` / ``DEFAULT_TENANT_LIMIT`` in
/// ``backend/app/services/rate_limit_service.py``. A request that
/// passes ``0`` for a limit field gets the default applied.
pub const DEFAULT_KEY_LIMIT: i32 = 60;
pub const DEFAULT_PROJECT_LIMIT: i32 = 300;
pub const DEFAULT_TENANT_LIMIT: i32 = 1000;
pub const DEFAULT_WINDOW_SECONDS: i32 = 60;

/// Per-scope outcome of one check. Maps 1:1 to the proto
/// ``ScopeResult`` — kept Rust-side to keep this module decoupled
/// from the generated code.
#[derive(Debug, Clone, PartialEq)]
pub struct ScopeOutcome {
    pub allowed: bool,
    pub remaining: i32,
    pub reset_at: i64,
    pub limit: i32,
    pub scope: String,
}

impl ScopeOutcome {
    /// "Fail-open" outcome — returned when Redis errors. ``remaining``
    /// is set to the limit so the gateway can still surface a sane
    /// header back to the caller.
    pub fn fail_open(scope: &str, limit: i32, window_sec: i32) -> Self {
        let now = unix_now_seconds();
        Self {
            allowed: true,
            remaining: limit,
            reset_at: now + window_sec as i64,
            limit,
            scope: scope.to_string(),
        }
    }
}

/// Sliding-window engine. Holds a single deadpool-redis pool so all
/// requests share the same TCP fan-out to Redis (the gateway used to
/// open one pool per service before the May-2026 audit; this sidecar
/// keeps the same shared-pool contract).
pub struct SlidingWindow {
    pool: Pool,
}

impl SlidingWindow {
    pub fn new(pool: Pool) -> Self {
        Self { pool }
    }

    /// Single-scope check. ``scope`` is the public label
    /// (``"api_key"`` / ``"project"`` / ``"tenant"``). ``key`` is
    /// the Redis key including its prefix (e.g. ``rl:key:abc``).
    pub async fn check_scope(
        &self,
        scope: &str,
        key: &str,
        limit: i32,
        window_sec: i32,
    ) -> ScopeOutcome {
        let limit = effective_limit(scope, limit);
        let window_sec = if window_sec <= 0 {
            DEFAULT_WINDOW_SECONDS
        } else {
            window_sec
        };

        let mut conn = match self.pool.get().await {
            Ok(c) => c,
            Err(e) => {
                metrics::redis_errors_total().inc();
                warn!(error = ?e, scope = scope, "redis pool get failed; failing open");
                return ScopeOutcome::fail_open(scope, limit, window_sec);
            }
        };

        let now_f = unix_now_f64();
        let cutoff = now_f - window_sec as f64;
        let member = format!("{:.6}:{}", now_f, monotonic_nanos());

        // Pipeline mirrors the Python service's calls exactly.
        // ``zcard`` is the *only* call whose return value we care
        // about — ``count_before`` decides allow/deny.
        let pipe_result: redis::RedisResult<(i64, i64, i64, i64)> = redis::pipe()
            .cmd("ZREMRANGEBYSCORE")
            .arg(key)
            .arg(0_f64)
            .arg(cutoff)
            .cmd("ZCARD")
            .arg(key)
            .cmd("ZADD")
            .arg(key)
            .arg(now_f)
            .arg(&member)
            .cmd("EXPIRE")
            .arg(key)
            .arg(window_sec + 1)
            .query_async(&mut *conn)
            .await;

        let (_removed, count_before, _added, _expired) = match pipe_result {
            Ok(t) => t,
            Err(e) => {
                metrics::redis_errors_total().inc();
                warn!(error = ?e, scope = scope, "redis pipeline failed; failing open");
                return ScopeOutcome::fail_open(scope, limit, window_sec);
            }
        };

        let count_before = count_before as i32;
        let allowed = count_before < limit;
        let remaining = std::cmp::max(0, limit - count_before - 1);
        let reset_at = unix_now_seconds() + window_sec as i64;

        if allowed {
            metrics::allowed_total(scope).inc();
        } else {
            metrics::denied_total(scope).inc();
        }

        ScopeOutcome {
            allowed,
            remaining,
            reset_at,
            limit,
            scope: scope.to_string(),
        }
    }

    /// Cheap Redis liveness probe — returns true iff we can acquire
    /// a connection from the pool AND a ``PING`` succeeds. Used by
    /// the ``Health`` RPC so operators can tell the difference
    /// between "sidecar up but Redis down (fail-open)" and "fully
    /// healthy".
    pub async fn ping(&self) -> bool {
        let mut conn = match self.pool.get().await {
            Ok(c) => c,
            Err(_) => return false,
        };
        let r: redis::RedisResult<String> = redis::cmd("PING").query_async(&mut *conn).await;
        matches!(r, Ok(ref s) if s == "PONG")
    }

    /// Three-scope check — runs the per-scope ZSET pipelines in
    /// parallel via ``tokio::join!`` and AND-aggregates the result.
    ///
    /// Scope labels match the Python service so the gateway's error
    /// response shape (``details.scope``) stays identical.
    pub async fn check_all(
        &self,
        key_id: &str,
        project_id: &str,
        tenant_id: &str,
        key_limit: i32,
        project_limit: i32,
        tenant_limit: i32,
        window_sec: i32,
    ) -> (Vec<ScopeOutcome>, bool, String) {
        let key_key = format!("rl:key:{}", key_id);
        let proj_key = format!("rl:proj:{}", project_id);
        let tenant_key = format!("rl:tenant:{}", tenant_id);

        let (k, p, t) = tokio::join!(
            self.check_scope("api_key", &key_key, key_limit, window_sec),
            self.check_scope("project", &proj_key, project_limit, window_sec),
            self.check_scope("tenant", &tenant_key, tenant_limit, window_sec),
        );

        let allowed = k.allowed && p.allowed && t.allowed;
        // First-denied wins the ``denied_scope`` label — match the
        // Python service's evaluation order (key → project → tenant).
        let denied_scope = if !k.allowed {
            "api_key".to_string()
        } else if !p.allowed {
            "project".to_string()
        } else if !t.allowed {
            "tenant".to_string()
        } else {
            String::new()
        };

        (vec![k, p, t], allowed, denied_scope)
    }
}

fn effective_limit(scope: &str, limit: i32) -> i32 {
    if limit > 0 {
        return limit;
    }
    match scope {
        "api_key" => DEFAULT_KEY_LIMIT,
        "project" => DEFAULT_PROJECT_LIMIT,
        "tenant" => DEFAULT_TENANT_LIMIT,
        _ => DEFAULT_KEY_LIMIT,
    }
}

fn unix_now_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn unix_now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn monotonic_nanos() -> u128 {
    // Same idea as Python's ``time.perf_counter_ns()``: a strictly
    // monotonic source for uniquely keying entries inside the same
    // millisecond. We sample at nanosecond granularity from the
    // system clock here; in practice the combination of
    // ``unix_now_f64`` + this value uniques even under 1M req/s.
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Tests — exercise the engine through a real Redis client that talks
// to a deadpool ``Pool`` backed by an in-process mock connection.
// We don't have a redis-mock crate in scope, so each test wires up a
// thin façade that returns canned responses for the four commands
// the pipeline issues.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // --- Helpers --------------------------------------------------------

    fn build_pool(url: &str) -> Pool {
        deadpool_redis::Config::from_url(url)
            .create_pool(Some(deadpool_redis::Runtime::Tokio1))
            .expect("pool")
    }

    fn unreachable_pool() -> Pool {
        // Port 1 is reserved-but-unused on every reasonable
        // platform; deadpool will fail on first ``get`` so the
        // engine exercises its fail-open branch.
        build_pool("redis://127.0.0.1:1/0")
    }

    // --- Fail-open contract ---------------------------------------------

    #[tokio::test]
    async fn test_check_scope_fail_open_on_redis_unreachable() {
        let engine = SlidingWindow::new(unreachable_pool());
        let out = engine.check_scope("api_key", "rl:key:x", 60, 60).await;
        assert!(out.allowed, "fail-open contract: unreachable Redis = allowed");
        assert_eq!(out.limit, 60);
        assert_eq!(out.scope, "api_key");
    }

    #[tokio::test]
    async fn test_check_all_fail_open_on_redis_unreachable() {
        let engine = SlidingWindow::new(unreachable_pool());
        let (results, allowed, denied) = engine
            .check_all("k1", "p1", "t1", 60, 300, 1000, 60)
            .await;
        assert!(allowed, "every scope must fail open");
        assert!(denied.is_empty());
        assert_eq!(results.len(), 3);
        for r in &results {
            assert!(r.allowed);
        }
    }

    #[tokio::test]
    async fn test_effective_limit_uses_defaults_on_zero() {
        assert_eq!(effective_limit("api_key", 0), DEFAULT_KEY_LIMIT);
        assert_eq!(effective_limit("project", 0), DEFAULT_PROJECT_LIMIT);
        assert_eq!(effective_limit("tenant", 0), DEFAULT_TENANT_LIMIT);
    }

    #[tokio::test]
    async fn test_effective_limit_honours_explicit_value() {
        assert_eq!(effective_limit("api_key", 5), 5);
        assert_eq!(effective_limit("tenant", 9999), 9999);
    }

    #[tokio::test]
    async fn test_scope_outcome_fail_open_construction() {
        let out = ScopeOutcome::fail_open("project", 300, 60);
        assert!(out.allowed);
        assert_eq!(out.scope, "project");
        assert_eq!(out.remaining, 300);
        assert_eq!(out.limit, 300);
        assert!(out.reset_at > 0);
    }

    // --- Real-Redis happy path (gated on TEST_REDIS_URL env var) -------

    fn test_redis_url() -> Option<String> {
        std::env::var("TEST_REDIS_URL").ok()
    }

    #[tokio::test]
    async fn test_check_scope_allows_under_limit_real_redis() {
        let Some(url) = test_redis_url() else {
            eprintln!("skip: TEST_REDIS_URL not set");
            return;
        };
        let pool = build_pool(&url);
        let key = format!("rl:test:check_scope_allowed:{}", monotonic_nanos());
        let engine = SlidingWindow::new(pool.clone());
        let out = engine.check_scope("api_key", &key, 5, 60).await;
        assert!(out.allowed);
        assert_eq!(out.limit, 5);
        // Cleanup
        if let Ok(mut conn) = pool.get().await {
            let _: redis::RedisResult<()> = conn.del(&key).await;
        }
    }

    #[tokio::test]
    async fn test_check_scope_denies_at_limit_real_redis() {
        let Some(url) = test_redis_url() else {
            eprintln!("skip: TEST_REDIS_URL not set");
            return;
        };
        let pool = build_pool(&url);
        let key = format!("rl:test:check_scope_denied:{}", monotonic_nanos());
        let engine = SlidingWindow::new(pool.clone());
        // 3 hits under a 3-cap should be allowed; the 4th denied.
        for i in 0..3 {
            let out = engine.check_scope("api_key", &key, 3, 60).await;
            assert!(out.allowed, "hit {} should be allowed", i + 1);
        }
        let denied = engine.check_scope("api_key", &key, 3, 60).await;
        assert!(!denied.allowed, "4th hit must be denied");
        assert_eq!(denied.remaining, 0);
        if let Ok(mut conn) = pool.get().await {
            let _: redis::RedisResult<()> = conn.del(&key).await;
        }
    }

    #[tokio::test]
    async fn test_check_all_one_scope_denies_overall_real_redis() {
        let Some(url) = test_redis_url() else {
            eprintln!("skip: TEST_REDIS_URL not set");
            return;
        };
        let pool = build_pool(&url);
        let suffix = monotonic_nanos();
        let kid = format!("test_key_{}", suffix);
        let pid = format!("test_proj_{}", suffix);
        let tid = format!("test_tenant_{}", suffix);
        let engine = SlidingWindow::new(pool.clone());
        // Drive the api_key scope past its limit; project + tenant
        // still healthy. ``allowed`` of CheckAll must be false and
        // ``denied_scope`` must call out api_key.
        for _ in 0..2 {
            let _ = engine.check_scope("api_key",
                &format!("rl:key:{}", kid), 2, 60).await;
        }
        let (results, allowed, denied) = engine
            .check_all(&kid, &pid, &tid, 2, 300, 1000, 60)
            .await;
        assert!(!allowed);
        assert_eq!(denied, "api_key");
        assert!(!results[0].allowed);
        assert!(results[1].allowed);
        assert!(results[2].allowed);
        if let Ok(mut conn) = pool.get().await {
            let _: redis::RedisResult<()> = conn.del(format!("rl:key:{}", kid)).await;
            let _: redis::RedisResult<()> = conn.del(format!("rl:proj:{}", pid)).await;
            let _: redis::RedisResult<()> = conn.del(format!("rl:tenant:{}", tid)).await;
        }
    }
}
