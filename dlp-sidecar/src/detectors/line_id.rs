//! LINE handle detector — port of ``line_id.py``.
//!
//! `\bLINE\s*(?:ID)?\s*[:=]?\s*(?P<value>@[a-zA-Z][a-zA-Z0-9_.\-]{2,19})\b`
//! with case-insensitive flag. Returns start/end of the value group
//! only (matching the Python contract).

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static LINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\bLINE\s*(?:ID)?\s*[:=]?\s*(?P<value>@[a-zA-Z][a-zA-Z0-9_.\-]{2,19})\b",
    )
    .expect("line_id regex")
});

pub struct LineIdDetector;

impl Detector for LineIdDetector {
    fn name(&self) -> &'static str {
        "line_id"
    }
    fn entity_type(&self) -> &'static str {
        "line_id"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2  // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in LINE_RE.captures_iter(text).flatten() {
            let g = match caps.name("value") {
                Some(g) => g,
                None => continue,
            };
            out.push(RawMatch {
                start: byte_to_char(text, g.start()),
                end: byte_to_char(text, g.end()),
                matched_text: g.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
