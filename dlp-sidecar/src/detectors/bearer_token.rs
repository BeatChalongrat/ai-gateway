//! Bearer-token / API-key detector — port of ``bearer_token.py``.
//!
//! Five sub-patterns, each with its own entity_type. The OpenAI
//! pattern uses a negative lookahead (`(?!ant-)`) — fancy-regex
//! required. All matches share CRITICAL severity and "secret"
//! category.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

struct SubPattern {
    entity_type: &'static str,
    re: Lazy<Regex>,
}

// One entry per Python tuple in ``_PATTERNS``. Order matters for
// deterministic output — we walk in Python's order so the parity
// test sees identical sequences.
static PATTERNS: [SubPattern; 5] = [
    SubPattern {
        entity_type: "bearer_token",
        // Python: `\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b` with re.ASCII.
        // ASCII flag affects \s and \b inside Python. fancy-regex
        // treats these as ASCII by default unless unicode flags are
        // set, so the bare pattern is equivalent.
        re: Lazy::new(|| Regex::new(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b").expect("bearer")),
    },
    SubPattern {
        entity_type: "aws_access_key",
        re: Lazy::new(|| Regex::new(r"\bAKIA[A-Z0-9]{16}\b").expect("aws")),
    },
    SubPattern {
        entity_type: "openai_api_key",
        re: Lazy::new(|| {
            Regex::new(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9\-_]{20,}\b").expect("openai")
        }),
    },
    SubPattern {
        entity_type: "github_token",
        re: Lazy::new(|| Regex::new(r"\b(?:ghp|gho|ghs)_[A-Za-z0-9]{36,}\b").expect("github")),
    },
    SubPattern {
        entity_type: "slack_token",
        re: Lazy::new(|| Regex::new(r"\bxox[bp]-[A-Za-z0-9\-]{10,}\b").expect("slack")),
    },
];

/// Five back-to-back Detector implementations would mean five Lazy
/// statics + five trait impls; we collapse into one detector that
/// emits matches across all sub-patterns. The parity test only
/// needs ``detector_name="bearer_token"`` on every emitted match,
/// which is what the Python class does too.
///
/// ``entity_type`` differs per sub-pattern — the parity fixture
/// asserts on it directly via the detector_name + match shape.
pub struct BearerTokenDetector;

impl Detector for BearerTokenDetector {
    fn name(&self) -> &'static str {
        "bearer_token"
    }
    fn entity_type(&self) -> &'static str {
        // The trait method returns the *default* entity_type;
        // the engine that translates RawMatch → DetectionMatch in
        // Batch 4 will overlay per-match entity_type for the cases
        // where ``detect_with_types`` is used. For parity testing
        // we only assert start/end/count so the placeholder is fine.
        "bearer_token"
    }
    fn category(&self) -> &'static str {
        "secret"
    }
    fn severity(&self) -> u32 {
        4  // CRITICAL
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for sub in PATTERNS.iter() {
            for m in sub.re.find_iter(text).flatten() {
                out.push(RawMatch {
                    start: byte_to_char(text, m.start()),
                    end: byte_to_char(text, m.end()),
                    matched_text: m.as_str().to_string(),
                    confidence: 1.0,
                    priority: 0,
                });
            }
        }
        out
    }
}

// Per-sub-pattern entity_type lookup is deferred until Batch 4
// (the gRPC wire format needs to populate ``entity_type`` per
// match). For Batch 1 parity testing the (start, end, count)
// surface is sufficient and the entity_type field on each
// ``SubPattern`` stays private + unread.
#[allow(dead_code)]
impl SubPattern {
    pub(crate) fn entity_type_name(&self) -> &'static str {
        self.entity_type
    }
}
