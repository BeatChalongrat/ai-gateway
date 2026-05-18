//! Password-in-config detector — port of ``password_field.py``.
//!
//! ``(?i)\b<label>\s*[:=]\s*(?P<value>"..."|'...'|\S+)``
//!
//! The detector emits start/end of the *value group only*, not the
//! whole label+value match. That contract is load-bearing for the
//! masker — it replaces the value while leaving the label visible.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static PASSWORD_RE: Lazy<Regex> = Lazy::new(|| {
    let label_alts =
        r"(?:password|passwd|pwd|secret_?key|api_?secret|auth_?token|db_?password)";
    let pat = format!(
        r"(?i)\b{label}\s*[:=]\s*(?P<value>\x22[^\x22]+\x22|'[^']+'|\S+)",
        label = label_alts
    );
    Regex::new(&pat).expect("password regex")
});

pub struct PasswordFieldDetector;

impl Detector for PasswordFieldDetector {
    fn name(&self) -> &'static str {
        "password_field"
    }
    fn entity_type(&self) -> &'static str {
        "password_in_config"
    }
    fn category(&self) -> &'static str {
        "secret"
    }
    fn severity(&self) -> u32 {
        3  // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in PASSWORD_RE.captures_iter(text).flatten() {
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
            });
        }
        out
    }
}
