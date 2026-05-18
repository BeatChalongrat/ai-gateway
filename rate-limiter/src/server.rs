//! gRPC server adapter — translates between the generated tonic
//! types and the engine in :mod:`sliding_window`.

use std::time::Instant;

use tonic::{Request, Response, Status};

use crate::metrics;
use crate::pb::rate_limit_service_server::RateLimitService;
use crate::pb::{
    CheckAllRequest, CheckAllResponse, HealthRequest, HealthResponse, ScopeResult,
};
use crate::sliding_window::{ScopeOutcome, SlidingWindow};

pub struct RateLimitServer {
    engine: SlidingWindow,
}

impl RateLimitServer {
    pub fn new(engine: SlidingWindow) -> Self {
        Self { engine }
    }
}

fn outcome_to_proto(o: ScopeOutcome) -> ScopeResult {
    ScopeResult {
        allowed: o.allowed,
        remaining: o.remaining,
        reset_at: o.reset_at,
        limit: o.limit,
        scope: o.scope,
    }
}

#[tonic::async_trait]
impl RateLimitService for RateLimitServer {
    async fn check_all(
        &self,
        request: Request<CheckAllRequest>,
    ) -> Result<Response<CheckAllResponse>, Status> {
        let req = request.into_inner();
        let start = Instant::now();
        let (outcomes, allowed, denied_scope) = self
            .engine
            .check_all(
                &req.key_id,
                &req.project_id,
                &req.tenant_id,
                req.key_limit,
                req.project_limit,
                req.tenant_limit,
                req.window_seconds,
            )
            .await;
        let latency_ms = start.elapsed().as_secs_f64() * 1000.0;
        metrics::check_latency_ms().observe(latency_ms);

        let results: Vec<ScopeResult> = outcomes.into_iter().map(outcome_to_proto).collect();
        Ok(Response::new(CheckAllResponse {
            allowed,
            results,
            latency_ms,
            denied_scope,
        }))
    }

    async fn health(
        &self,
        _request: Request<HealthRequest>,
    ) -> Result<Response<HealthResponse>, Status> {
        // We do a cheap pool ``get`` to know whether Redis is
        // reachable. Failure flips ``redis_connected`` but the
        // overall status stays ``ready`` — the service is still
        // useful in fail-open mode.
        let redis_connected = self.engine.ping().await;
        Ok(Response::new(HealthResponse {
            status: "ready".into(),
            redis_connected,
        }))
    }
}
