//! API key / generic secret detector — port of ``api_key_secret.py``.
//!
//! Six standalone patterns + one assignment pattern; duplicates
//! (same start/end) are suppressed via ``seen_spans``. Patterns
//! that overlap (e.g. the long-form Anthropic key and the shorter
//! ``api03`` variant) emit in the Python order so the later
//! shorter form is dropped by the dedup, matching Python.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

struct StandalonePattern {
    re: Lazy<Regex>,
}

static STANDALONE: [StandalonePattern; 6] = [
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\bsk-ant-(?:api\d+-)?[A-Za-z0-9_\-]{32,}\b").expect("ant long")
        }),
    },
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\bsk-ant-(?:api03|admin01)-[A-Za-z0-9_\-]{12,}\b").expect("ant short")
        }),
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b").expect("openai proj")),
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bsk-live-[A-Za-z0-9_\-]{12,}\b").expect("openai live")),
    },
    StandalonePattern {
        re: Lazy::new(|| Regex::new(r"\bAIza[A-Za-z0-9_\-]{35}\b").expect("google")),
    },
    StandalonePattern {
        re: Lazy::new(|| {
            Regex::new(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b").expect("jwt")
        }),
    },
];

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
        let mut out = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();

        // Standalone patterns in Python order
        for sub in STANDALONE.iter() {
            for m in sub.re.find_iter(text).flatten() {
                let s = byte_to_char(text, m.start());
                let e = byte_to_char(text, m.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: m.as_str().to_string(),
                        confidence: 1.0,
                        priority: 0,
                    });
                }
            }
        }

        // Assignment pattern — emit the value group only
        for caps in ASSIGN_RE.captures_iter(text).flatten() {
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
                    });
                }
            }
        }
        out
    }
}
