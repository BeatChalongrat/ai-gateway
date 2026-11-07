//! API key / generic secret detector — port of ``api_key_secret.py``.
//!
//! Eight standalone patterns + one assignment pattern; duplicates
//! (same start/end) are suppressed via ``seen_spans``. Patterns
//! that overlap (e.g. the long-form Anthropic key and the shorter
//! ``api03`` variant) emit in the Python order so the later
//! shorter form is dropped by the dedup, matching Python.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

struct StandalonePattern {
    re: Lazy<Regex>,
    // Per-pattern entity_type — mirrors the Python ``_PATTERNS`` tuples
    // so the Rust path emits the same distinct types (anthropic_api_key,
    // openai_project_key, jwt_token, github_pat, …) the classifier's
    // ``len(entity_types) >= 3`` escalation relies on.
    entity_type: &'static str,
}

static STANDALONE: [StandalonePattern; 8] = [
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\bsk-ant-(?:api\d+-)?[A-Za-z0-9_\-]{32,}\b").expect("ant long")
        }),
        entity_type: "anthropic_api_key",
    },
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\bsk-ant-(?:api03|admin01)-[A-Za-z0-9_\-]{12,}\b").expect("ant short")
        }),
        entity_type: "anthropic_api_key",
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b").expect("openai proj")),
        entity_type: "openai_project_key",
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bsk-live-[A-Za-z0-9_\-]{12,}\b").expect("openai live")),
        entity_type: "openai_live_key",
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bAIza[A-Za-z0-9_\-]{35}\b").expect("google")),
        entity_type: "google_api_key",
    },
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b").expect("jwt")
        }),
        entity_type: "jwt_token",
    },
    StandalonePattern {
        // GitHub classic PAT: ghp_ + exactly 36 alphanumerics.
        re: Lazy::new(|| {
            Regex::new(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9])")
                .expect("github classic pat")
        }),
        entity_type: "github_pat",
    },
    StandalonePattern {
        // GitHub fine-grained PAT: github_pat_ + 82 [A-Za-z0-9_].
        re: Lazy::new(|| {
            Regex::new(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{82}(?![A-Za-z0-9])")
                .expect("github fine-grained pat")
        }),
        entity_type: "github_pat",
    },
];

// Short-form Anthropic key — only scanned on base64-decoded content
// (from_decoded), mirroring Python's ``_DECODED_PATTERNS``. The 8-char
// lower bound is too loose for general text but appropriate for an
// isolated decoded payload (the segment is already known to be a hidden
// blob, so a leaked truncated key must still be caught).
static DECODED_ANTHROPIC_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b").expect("ant decoded short"));

// Generic assignment pattern. Python uses (?ix) verbose; we expand
// to a single non-verbose pattern.
static ASSIGN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r#"(?i)\b(?:password|passwd|pwd|api[_\-]?key|secret(?:[_\-]?key)?|access[_\-]?token|aws[_\-]?secret[_\-]?access[_\-]?key|auth[_\-]?token)\s*[:=]\s*["']?(?P<value>[A-Za-z0-9+/_\-]{12,})"#,
    )
    .expect("assign")
});

pub struct ApiKeySecretDetector;

impl Detector for ApiKeySecretDetector {
    fn name(&self) -> &'static str {
        "api_key_secret"
    }
    fn entity_type(&self) -> &'static str {
        "api_key_secret"
    }
    fn category(&self) -> &'static str {
        "credential"
    }
    fn severity(&self) -> u32 {
        4 // CRITICAL
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        self.detect_ctx(text, false)
    }

    fn detect_ctx(&self, text: &str, from_decoded: bool) -> Vec<RawMatch> {
        let mut out = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();

        // Standalone patterns in Python order
        for sub in STANDALONE.iter() {
            for m in sub.re.find_iter(text).engine_ok() {
                let s = byte_to_char(text, m.start());
                let e = byte_to_char(text, m.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: m.as_str().to_string(),
                        confidence: 1.0,
                        priority: 0,
                        entity_type: Some(sub.entity_type),
                    });
                }
            }
        }

        // Decoded-only short Anthropic key — mirrors Python's
        // ``if from_decoded: _DECODED_PATTERNS``. Runs after the
        // standalone patterns so a full-length key already seen wins
        // the (start,end) dedup; a short/truncated key in an isolated
        // base64 payload is caught here.
        if from_decoded {
            for m in DECODED_ANTHROPIC_RE.find_iter(text).engine_ok() {
                let s = byte_to_char(text, m.start());
                let e = byte_to_char(text, m.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: m.as_str().to_string(),
                        confidence: 1.0,
                        priority: 0,
                        entity_type: Some("anthropic_api_key"),
                    });
                }
            }
        }

        // Assignment pattern — emit the value group only
        for caps in ASSIGN_RE.captures_iter(text).engine_ok() {
            if let Some(g) = caps.name("value") {
                let s = byte_to_char(text, g.start());
                let e = byte_to_char(text, g.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: g.as_str().to_string(),
                        confidence: 1.0,
                        priority: 0,
                        entity_type: Some("credential_assignment"),
                    });
                }
            }
        }
        out
    }
}
