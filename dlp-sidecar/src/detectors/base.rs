//! Detector trait — mirrors ``app.services.dlp.detectors.base.BaseDetector``.
//!
//! Every Rust detector implements ``Detector::detect`` returning a
//! flat list of :struct:`RawMatch`. The parity test (and eventually
//! the gRPC server) wraps each match with the metadata pulled from
//! the trait methods (entity_type / category / severity / priority).
//!
//! Offsets in ``RawMatch`` are CHARACTER (code-point) offsets — same
//! semantics as Python's ``re.finditer``. Rust's regex crates return
//! byte offsets, so each detector calls :fn:`byte_to_char` before
//! emitting a match. For pure-ASCII inputs the conversion is a
//! no-op; for multi-byte inputs (Thai, emoji) it is load-bearing.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RawMatch {
    pub start: usize,
    pub end: usize,
    pub matched_text: String,
    pub confidence: f32,
    pub priority: i32,
}

pub trait Detector: Send + Sync {
    fn name(&self) -> &'static str;
    fn entity_type(&self) -> &'static str;
    fn category(&self) -> &'static str;
    /// 0=NONE 1=LOW 2=MEDIUM 3=HIGH 4=CRITICAL
    fn severity(&self) -> u32;
    fn priority(&self) -> i32 {
        0
    }
    fn detect(&self, text: &str) -> Vec<RawMatch>;
}

/// Convert a byte offset into a character (code-point) offset.
///
/// Rust regex crates index by byte; Python ``re`` indexes by code
/// point. The two diverge once the input contains a multi-byte
/// character before the match start. We pay the conversion cost on
/// every emitted match because the parity contract is keyed on
/// Python-style offsets — operator dashboards and the masker both
/// rely on those being identical across engines.
pub fn byte_to_char(text: &str, byte_offset: usize) -> usize {
    if byte_offset == 0 {
        return 0;
    }
    if byte_offset >= text.len() {
        return text.chars().count();
    }
    text[..byte_offset].chars().count()
}
