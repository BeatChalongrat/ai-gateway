//! dlp-sidecar entry point — Batch 1 ships a minimal stub server
//! that returns ``UNIMPLEMENTED`` on the scan RPCs but answers
//! ``Health`` so docker-compose's healthcheck succeeds. The real
//! request → DetectionMatch pipeline lands in Batch 4.

use std::convert::Infallible;
use std::env;
use std::net::SocketAddr;

use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use tonic::transport::Server as TonicServer;
use tonic::{Request as RpcRequest, Response as RpcResponse, Status};
use tracing::info;

use dlp_sidecar::registry;

pub mod pb {
    tonic::include_proto!("dlp");
}

use pb::dlp_service_server::{DlpService, DlpServiceServer};
use pb::{
    HealthRequest, HealthResponse, MessagesRequest, ScanRequest, ScanResponse,
};

fn env_or(key: &str, fallback: &str) -> String {
    env::var(key).unwrap_or_else(|_| fallback.into())
}

#[derive(Default)]
struct DlpServer;

#[tonic::async_trait]
impl DlpService for DlpServer {
    async fn scan_text(
        &self,
        _request: RpcRequest<ScanRequest>,
    ) -> Result<RpcResponse<ScanResponse>, Status> {
        // Batch 1: scaffolding only. The full RawMatch →
        // DetectionMatch translation arrives with Batch 4 when the
        // gRPC server is wired into the registry.
        Err(Status::unimplemented(
            "DlpService.ScanText: not implemented until Batch 4",
        ))
    }

    async fn scan_messages(
        &self,
        _request: RpcRequest<MessagesRequest>,
    ) -> Result<RpcResponse<ScanResponse>, Status> {
        Err(Status::unimplemented(
            "DlpService.ScanMessages: not implemented until Batch 4",
        ))
    }

    async fn health(
        &self,
        _request: RpcRequest<HealthRequest>,
    ) -> Result<RpcResponse<HealthResponse>, Status> {
        Ok(RpcResponse::new(HealthResponse {
            status: "ready".into(),
            detector_count: registry::default_detectors().len() as i32,
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
    info!(grpc_port, metrics_port, "dlp-sidecar booting (Batch 1 stub)");

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
            .body(Body::from(
                "# Batch 1 — metrics added in Batch 4\n",
            ))
            .unwrap()),
        _ => Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("not found\n"))
            .unwrap()),
    }
}
