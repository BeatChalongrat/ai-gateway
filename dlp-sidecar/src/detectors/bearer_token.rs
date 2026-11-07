//! Bearer-token / API-key detector — port of ``bearer_token.py``.
//!
//! Five sub-patterns, each with its own entity_type. The OpenAI
//! pattern uses a negative lookahead (`(?!ant-)`) — fancy-regex
//! required. All matches share CRITICAL severity and "secret"
//! category.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

struct SubPattern {
    entity_type: &'static str,
    re: Lazy<Regex>,
}

// One entry per Python tuple in ``_PATTERNS``. Order matters for
// deterministic output — we walk in Python's order so the parity
// test sees identical sequences.
static PATTERNS: [SubPattern; 6] = [
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
        // Bug 4: match on the sk- PREFIX (the discriminator) with a low body
        // floor (6) instead of a 20-char length threshold that let a
        // short-but-real key (sk-test-abc123) egress raw. Mirrors
        // bearer_token.py — keep the two in lockstep for the parity test.
        re: Lazy::new(|| {
            Regex::new(r"\bsk-(?!ant-)(?:proj-|live-)?[A-Za-z0-9\-_]{6,}\b").expect("openai")
        }),
    },
    SubPattern {
        entity_type: "gateway_api_key",
        // gw_ — this gateway's own API-key prefix; a leaked gw_ key is a
        // credential. Prefix-match like the sk- family.
        re: Lazy::new(|| Regex::new(r"\bgw_[A-Za-z0-9\-_]{6,}\b").expect("gateway")),
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
        // Default/fallback only. Each match carries its real per-pattern
        // entity_type via RawMatch.entity_type (Some(sub.entity_type)),
        // which the server overlays over this — matching Python's
        // per-tuple entity_type in _PATTERNS.
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
            for m in sub.re.find_iter(text).engine_ok() {
                out.push(RawMatch {
                    start: byte_to_char(text, m.start()),
                    end: byte_to_char(text, m.end()),
                    matched_text: m.as_str().to_string(),
                    confidence: 1.0,
                    priority: 0,
                    entity_type: Some(sub.entity_type),
                });
            }
        }
        out
    }
}
