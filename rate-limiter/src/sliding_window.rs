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

use std::sync::atomic::{AtomicU64, Ordering};
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

/// Atomic all-or-nothing sliding-window check across the 3 scopes in a
/// single Redis EVAL — byte-identical to the Python
/// ``_CHECK_ALL_LUA``. The decision is computed from the ZCARDs BEFORE
/// any ZADD, and ZADD happens only when allowed, so concurrent bursts
/// cannot over-admit (Redis runs the script single-threaded). KEYS are
/// parameterised for Redis-Cluster compatibility.
///   KEYS[1..3] = rl:key, rl:proj, rl:tenant
///   ARGV       = now, cutoff, key_limit, proj_limit, tenant_limit, expire, member
///   returns    = {denied(0|1|2|3), c1, c2, c3, rem1, rem2, rem3}
const CHECK_ALL_LUA: &str = r#"
local cutoff = ARGV[2]
local l1 = tonumber(ARGV[3]); local l2 = tonumber(ARGV[4]); local l3 = tonumber(ARGV[5])
local expire = tonumber(ARGV[6]); local member = ARGV[7]
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff); local c1 = redis.call('ZCARD', KEYS[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, cutoff); local c2 = redis.call('ZCARD', KEYS[2])
redis.call('ZREMRANGEBYSCORE', KEYS[3], 0, cutoff); local c3 = redis.call('ZCARD', KEYS[3])
local denied = 0
if c1 >= l1 then denied = 1 elseif c2 >= l2 then denied = 2 elseif c3 >= l3 then denied = 3 end
if denied == 0 then
  redis.call('ZADD', KEYS[1], ARGV[1], member); redis.call('EXPIRE', KEYS[1], expire)
  redis.call('ZADD', KEYS[2], ARGV[1], member); redis.call('EXPIRE', KEYS[2], expire)
  redis.call('ZADD', KEYS[3], ARGV[1], member); redis.call('EXPIRE', KEYS[3], expire)
end
local add = 0; if denied == 0 then add = 1 end
local r1 = l1 - c1 - add; if r1 < 0 then r1 = 0 end
local r2 = l2 - c2 - add; if r2 < 0 then r2 = 0 end
local r3 = l3 - c3 - add; if r3 < 0 then r3 = 0 end
return {denied, c1, c2, c3, r1, r2, r3}
"#;

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
        let member = format!("{:.6}:{}", now_f, member_suffix());

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

    /// Three-scope check — ATOMIC via a single Lua EVAL (all-or-nothing).
    /// No over-admission race: the decision is made from the ZCARDs
    /// before any ZADD, and ZADD only fires when allowed. Fail-open on
    /// any Redis error. Scope labels match the Python service.
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
        let l1 = effective_limit("api_key", key_limit);
        let l2 = effective_limit("project", project_limit);
        let l3 = effective_limit("tenant", tenant_limit);
        let window_sec = if window_sec <= 0 {
            DEFAULT_WINDOW_SECONDS
        } else {
            window_sec
        };
        let key_key = format!("rl:key:{}", key_id);
        let proj_key = format!("rl:proj:{}", project_id);
        let tenant_key = format!("rl:tenant:{}", tenant_id);
        let scopes = ["api_key", "project", "tenant"];
        let limits = [l1, l2, l3];

        let fail_open = || {
            (
                vec![
                    ScopeOutcome::fail_open("api_key", l1, window_sec),
                    ScopeOutcome::fail_open("project", l2, window_sec),
                    ScopeOutcome::fail_open("tenant", l3, window_sec),
                ],
                true,
                String::new(),
            )
        };

        let mut conn = match self.pool.get().await {
            Ok(c) => c,
            Err(e) => {
                metrics::redis_errors_total().inc();
                warn!(error = ?e, "redis pool get failed; failing open");
                return fail_open();
            }
        };

        let now_f = unix_now_f64();
        let cutoff = now_f - window_sec as f64;
        let member = format!("{:.6}:{}", now_f, member_suffix());

        let script = redis::Script::new(CHECK_ALL_LUA);
        let res: redis::RedisResult<Vec<i64>> = script
            .key(&key_key)
            .key(&proj_key)
            .key(&tenant_key)
            .arg(format!("{:.6}", now_f))
            .arg(format!("{:.6}", cutoff))
            .arg(l1)
            .arg(l2)
            .arg(l3)
            .arg(window_sec + 1)
            .arg(&member)
            .invoke_async(&mut *conn)
            .await;

        let out = match res {
            Ok(v) if v.len() == 7 => v,
            Ok(_) | Err(_) => {
                metrics::redis_errors_total().inc();
                warn!("rate-limit EVAL failed; failing open");
                return fail_open();
            }
        };

        let denied = out[0];
        let counts = [out[1], out[2], out[3]];
        let rems = [out[4], out[5], out[6]];
        let reset_at = unix_now_seconds() + window_sec as i64;

        let mut outcomes = Vec::with_capacity(3);
        for i in 0..3 {
            let scope_allowed = counts[i] < limits[i] as i64;
            if scope_allowed {
                metrics::allowed_total(scopes[i]).inc();
            } else {
                metrics::denied_total(scopes[i]).inc();
            }
            outcomes.push(ScopeOutcome {
                allowed: scope_allowed,
                remaining: rems[i] as i32,
                reset_at,
                limit: limits[i],
                scope: scopes[i].to_string(),
            });
        }

        let allowed = denied == 0;
        let denied_scope = if denied == 0 {
            String::new()
        } else {
            scopes[(denied - 1) as usize].to_string()
        };

        (outcomes, allowed, denied_scope)
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

/// Process-global, ever-increasing counter used as the unique suffix of
/// every ZSET member. The ZADD *score* stays the wall-clock ``now`` (the
/// sliding-window prune does ZREMRANGEBYSCORE on a wall-clock cutoff, so
/// the score must remain wall-clock), but the *member* must be unique per
/// request or a second ZADD with the same member silently overwrites the
/// first's score instead of adding a new entry — undercounting the window
/// and over-admitting (re-audit finding H7).
static MEMBER_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Returns a strictly-increasing, process-unique suffix for the ZSET
/// member string. Unlike a wall-clock sample, this is guaranteed unique
/// regardless of clock resolution or NTP step-backs: every call returns a
/// distinct value (until the u64 wraps after 2^64 calls, which is not a
/// practical concern). Wrapping ``fetch_add`` so an overflow can never
/// panic.
fn member_suffix() -> u64 {
    MEMBER_COUNTER.fetch_add(1, Ordering::Relaxed)
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

    // --- Member uniqueness (H7 regression) ------------------------------

    #[tokio::test]
    async fn test_member_suffix_is_strictly_unique() {
        // The H7 bug: a wall-clock-sampled "nanos" could repeat within a
        // single clock tick (or step back under NTP), producing an
        // identical ZSET member → the second ZADD overwrites the first's
        // score instead of adding a new entry → window undercounts →
        // over-admission. An ever-increasing counter must never repeat.
        use std::collections::HashSet;
        const N: usize = 100_000;
        let mut seen = HashSet::with_capacity(N);
        for _ in 0..N {
            assert!(
                seen.insert(member_suffix()),
                "member_suffix() produced a duplicate value"
            );
        }
        assert_eq!(seen.len(), N);
    }

    #[tokio::test]
    async fn test_member_strings_are_distinct_within_same_tick() {
        // Build full member strings the way ``check_scope`` /
        // ``check_all`` do, holding ``now_f`` FIXED to simulate many
        // requests landing in the same wall-clock instant. They must all
        // be distinct thanks to the counter suffix.
        use std::collections::HashSet;
        const N: usize = 10_000;
        let now_f = unix_now_f64();
        let mut seen = HashSet::with_capacity(N);
        for _ in 0..N {
            let member = format!("{:.6}:{}", now_f, member_suffix());
            assert!(seen.insert(member), "duplicate member within same tick");
        }
        assert_eq!(seen.len(), N);
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
        let key = format!("rl:test:check_scope_allowed:{}", member_suffix());
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
        let key = format!("rl:test:check_scope_denied:{}", member_suffix());
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
        let suffix = member_suffix();
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
