//! Email address detector — port of ``email.py``.
//!
//! Python regex (verbatim):
//!     `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static EMAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}").expect("email regex")
});

pub struct EmailDetector;

impl Detector for EmailDetector {
    fn name(&self) -> &'static str {
        "email"
    }
    fn entity_type(&self) -> &'static str {
        "email_address"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2  // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in EMAIL_RE.find_iter(text).flatten() {
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
