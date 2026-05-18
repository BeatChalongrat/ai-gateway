//! Library facade for ``dlp-sidecar``.
//!
//! Exposes the detector modules so integration tests (and, in
//! Batch 4, the gRPC server) can dispatch through them without
//! pulling in the binary's entry-point.
//!
//! The proto module is intentionally NOT re-exported from here —
//! ``tonic::include_proto!`` lives in :mod:`main` because the
//! server impl is local to the binary. Future batches will move it
//! to the lib if any non-binary consumer needs it.

pub mod detectors;
pub mod registry;
