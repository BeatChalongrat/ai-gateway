//! dlp-sidecar entry point — Batch 4 wires the gRPC handlers to
//! the detector registry. NFKC-normalised input goes through every
//! registered detector; the resulting matches are mapped to the
//! proto ``DetectionMatch`` schema with per-detector metadata
//! (entity_type / category / severity / priority) attached.
//!
//! The handlers fail open: on a registry / regex panic we'd rather
//! return an empty match list than crash the gateway's hot path.
//! Inside the per-detector loop each ``detect()`` call is shielded
//! by ``catch_unwind`` so a single broken detector doesn't bring
//! down the whole scan.

use std::convert::Infallible;
use std::env;
use std::net::SocketAddr;
use std::time::Instant;

use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use tonic::transport::Server as TonicServer;
use tonic::{Request as RpcRequest, Response as RpcResponse, Status};
use tracing::{info, warn};
use unicode_normalization::UnicodeNormalization;

use dlp_sidecar::detectors::base::{Detector, RawMatch};
use dlp_sidecar::registry;

pub mod pb {
    tonic::include_proto!("dlp");
}

use pb::dlp_service_server::{DlpService, DlpServiceServer};
use pb::{
    DetectionMatch as ProtoMatch, HealthRequest, HealthResponse, MessagesRequest,
    ScanRequest, ScanResponse,
};

fn env_or(key: &str, fallback: &str) -> String {
    env::var(key).unwrap_or_else(|_| fallback.into())
}

#[derive(Default)]
struct DlpServer;

/// Normalise input the same way the Python ``Preprocessor`` does
/// upstream — NFKC canonical-decomposition + canonical-composition.
/// Keeps the Rust output offsets in the same coordinate system as
/// the Python detectors that consume the same normalised text.
fn nfkc(input: &str) -> String {
    input.nfkc().collect()
}

/// Run every detector in ``detectors`` over ``text`` and collect
/// raw matches paired with the detector handle (so we can attach
/// per-detector metadata when mapping to proto). Sorted by
/// ``(start, end)`` — matches Python's ``DetectionResult`` ordering.
fn run_registry(
    text: &str,
    detectors: &[Box<dyn Detector>],
    disabled: &[String],
) -> Vec<(String, &'static str, &'static str, u32, RawMatch)> {
    let mut out = Vec::new();
    for det in detectors {
        let name = det.name();
        if disabled.iter().any(|d| d == name) {
            continue;
        }
        let raw_matches = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            det.detect(text)
        }));
        match raw_matches {
            Ok(matches) => {
                let entity = det.entity_type();
                let category = det.category();
                let severity = det.severity();
                for m in matches {
                    out.push((name.to_string(), entity, category, severity, m));
                }
            }
            Err(_) => {
                warn!(detector = name, "detector panicked, skipping");
            }
        }
    }
    out.sort_by_key(|(_, _, _, _, m)| (m.start, m.end));
    out
}

fn to_proto(
    detector_name: String,
    entity: &'static str,
    category: &'static str,
    severity: u32,
    m: RawMatch,
) -> ProtoMatch {
    ProtoMatch {
        detector_name,
        entity_type: entity.to_string(),
        category: category.to_string(),
        start: m.start as i32,
        end: m.end as i32,
        matched_text: m.matched_text,
        severity: severity as i32,
        confidence: m.confidence,
        priority: m.priority,
        metadata_json: String::new(),
    }
}

#[tonic::async_trait]
impl DlpService for DlpServer {
    async fn scan_text(
        &self,
        request: RpcRequest<ScanRequest>,
    ) -> Result<RpcResponse<ScanResponse>, Status> {
        let req = request.into_inner();
        let t0 = Instant::now();
        let normalized = nfkc(&req.text);
        // Per-request registry composition so ``thai_name_no_prefix``
        // is registered iff NER is *off* — same gate Python uses.
        let detectors = registry::default_detectors(req.ner_enabled);
        let raw = run_registry(&normalized, &detectors, &req.disabled_detectors);
        let matches: Vec<ProtoMatch> = raw
            .into_iter()
            .map(|(n, e, c, s, m)| to_proto(n, e, c, s, m))
            .collect();
        let latency_ms = t0.elapsed().as_secs_f64() * 1000.0;
        Ok(RpcResponse::new(ScanResponse {
            matches,
            scanned_direction: req.language,
            scan_target: "text".into(),
            latency_ms,
        }))
    }

    async fn scan_messages(
        &self,
        request: RpcRequest<MessagesRequest>,
    ) -> Result<RpcResponse<ScanResponse>, Status> {
        let req = request.into_inner();
        let t0 = Instant::now();
        let detectors = registry::default_detectors(req.ner_enabled);

        // Scan each message independently — same contract as
        // Python's ``DetectorRegistry.scan_messages``: spans index
        // into the *individual* ``messages[i].content``, not into
        // any joined concatenation. The earlier ``base_char``
        // accumulator produced offsets that diverged from the
        // Python path's, causing constant ``only_rust`` /
        // ``only_python`` deltas in shadow-mode comparison.
        //
        // ``scan_system_messages=false`` skips role="system". Other
        // roles are always scanned.
        let mut all: Vec<(String, &'static str, &'static str, u32, RawMatch)> = Vec::new();
        for msg in req.messages.iter() {
            if !req.scan_system_messages && msg.role == "system" {
                continue;
            }
            let normalized = nfkc(&msg.content);
            let per_msg = run_registry(&normalized, &detectors, &req.disabled_detectors);
            all.extend(per_msg);
        }

        all.sort_by_key(|(_, _, _, _, m)| (m.start, m.end));
        let matches: Vec<ProtoMatch> = all
            .into_iter()
            .map(|(n, e, c, s, m)| to_proto(n, e, c, s, m))
            .collect();

        let latency_ms = t0.elapsed().as_secs_f64() * 1000.0;
        Ok(RpcResponse::new(ScanResponse {
            matches,
            scanned_direction: req.direction,
            scan_target: "messages".into(),
            latency_ms,
        }))
    }

    async fn health(
        &self,
        _request: RpcRequest<HealthRequest>,
    ) -> Result<RpcResponse<HealthResponse>, Status> {
        // Health/metrics count uses the NER-on composition (29) —
        // that's Python's default for ``get_default_registry``
        // (``ner_enabled: bool = True``) and is the right thing
        // for dashboards / alerts that compare against the Python
        // count.
        Ok(RpcResponse::new(HealthResponse {
            status: "ready".into(),
            detector_count: registry::default_detectors(true).len() as i32,
        }))
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "dlp_sidecar=info,tonic=info".into()),
        )
        .init();

    let grpc_port: u16 = env_or("GRPC_PORT", "50051").parse()?;
    let metrics_port: u16 = env_or("METRICS_PORT", "9102").parse()?;
    info!(
        grpc_port,
        metrics_port,
        detector_count = registry::default_detectors(true).len(),
        "dlp-sidecar booting"
    );

    // ---- HTTP /healthz + /metrics ----
    tokio::spawn(async move {
        let addr: SocketAddr = ([0, 0, 0, 0], metrics_port).into();
        let make_svc = make_service_fn(|_| async {
            Ok::<_, Infallible>(service_fn(http_handler))
        });
        let server = Server::bind(&addr).serve(make_svc);
        info!(%addr, "metrics+healthz listening");
        let _ = server.await;
    });

    // ---- gRPC ----
    let grpc_addr: SocketAddr = ([0, 0, 0, 0], grpc_port).into();
    info!(%grpc_addr, "gRPC listening");
    TonicServer::builder()
        .add_service(DlpServiceServer::new(DlpServer::default()))
        .serve_with_shutdown(grpc_addr, async {
            tokio::signal::ctrl_c().await.ok();
            info!("shutdown signal received");
        })
        .await?;

    Ok(())
}

async fn http_handler(req: Request<Body>) -> Result<Response<Body>, Infallible> {
    match (req.method(), req.uri().path()) {
        (&Method::GET, "/healthz") => Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "text/plain")
            .body(Body::from("ok\n"))
            .unwrap()),
        (&Method::GET, "/metrics") => Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "text/plain; version=0.0.4")
            .body(Body::from(format!(
                "# dlp-sidecar detector_count {}\ndlp_sidecar_detector_count {}\n",
                registry::default_detectors(true).len(),
                registry::default_detectors(true).len()
            )))
            .unwrap()),
        _ => Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("not found\n"))
            .unwrap()),
    }
}
