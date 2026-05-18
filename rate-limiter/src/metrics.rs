//! Prometheus metrics — registered once via ``once_cell``.
//!
//! Each public helper returns a metric handle that the engine /
//! server modules ``inc()`` directly. Histograms expose buckets
//! tuned for the latency the sliding-window pipeline actually
//! produces (sub-millisecond happy path → ~50ms tail on a Redis
//! restart).

use once_cell::sync::Lazy;
use prometheus::{
    Encoder, Histogram, HistogramOpts, IntCounter, IntCounterVec, Opts, Registry, TextEncoder,
};

pub static REGISTRY: Lazy<Registry> = Lazy::new(Registry::new);

static CHECK_LATENCY_MS: Lazy<Histogram> = Lazy::new(|| {
    let h = Histogram::with_opts(
        HistogramOpts::new(
            "rate_limiter_check_latency_ms",
            "End-to-end CheckAll RPC latency in milliseconds.",
        )
        .buckets(vec![
            0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0,
        ]),
    )
    .expect("histogram opts");
    REGISTRY.register(Box::new(h.clone())).expect("register latency");
    h
});

static REDIS_ERRORS_TOTAL: Lazy<IntCounter> = Lazy::new(|| {
    let c = IntCounter::new(
        "rate_limiter_redis_errors_total",
        "Lifetime count of Redis errors (pool acquisition or pipeline).",
    )
    .expect("counter opts");
    REGISTRY.register(Box::new(c.clone())).expect("register redis errors");
    c
});

static ALLOWED_TOTAL: Lazy<IntCounterVec> = Lazy::new(|| {
    let c = IntCounterVec::new(
        Opts::new(
            "rate_limiter_allowed_total",
            "Lifetime count of allowed checks per scope.",
        ),
        &["scope"],
    )
    .expect("counter vec opts");
    REGISTRY.register(Box::new(c.clone())).expect("register allowed");
    c
});

static DENIED_TOTAL: Lazy<IntCounterVec> = Lazy::new(|| {
    let c = IntCounterVec::new(
        Opts::new(
            "rate_limiter_denied_total",
            "Lifetime count of denied checks per scope.",
        ),
        &["scope"],
    )
    .expect("counter vec opts");
    REGISTRY.register(Box::new(c.clone())).expect("register denied");
    c
});

/// Touch every metric so /metrics reports them with a zero sample
/// on first scrape instead of being silent until the first call.
pub fn init() {
    let _ = &*CHECK_LATENCY_MS;
    let _ = &*REDIS_ERRORS_TOTAL;
    let _ = ALLOWED_TOTAL.with_label_values(&["api_key"]);
    let _ = ALLOWED_TOTAL.with_label_values(&["project"]);
    let _ = ALLOWED_TOTAL.with_label_values(&["tenant"]);
    let _ = DENIED_TOTAL.with_label_values(&["api_key"]);
    let _ = DENIED_TOTAL.with_label_values(&["project"]);
    let _ = DENIED_TOTAL.with_label_values(&["tenant"]);
}

pub fn check_latency_ms() -> &'static Histogram {
    &CHECK_LATENCY_MS
}
pub fn redis_errors_total() -> &'static IntCounter {
    &REDIS_ERRORS_TOTAL
}
pub fn allowed_total(scope: &str) -> prometheus::core::GenericCounter<prometheus::core::AtomicU64> {
    ALLOWED_TOTAL.with_label_values(&[scope])
}
pub fn denied_total(scope: &str) -> prometheus::core::GenericCounter<prometheus::core::AtomicU64> {
    DENIED_TOTAL.with_label_values(&[scope])
}

/// Serialize the registry contents to the Prometheus text format —
/// used by the ``/metrics`` HTTP handler in main.rs.
pub fn render() -> Vec<u8> {
    let encoder = TextEncoder::new();
    let mut buf = Vec::new();
    let mf = REGISTRY.gather();
    let _ = encoder.encode(&mf, &mut buf);
    buf
}
