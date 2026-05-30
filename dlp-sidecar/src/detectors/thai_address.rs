//! Thai address detector — port of ``thai_address.py``.
//!
//! Two-group regex (keyword + body). Span is start of group 1
//! through end of group 2 of the regex match (Python: m.start(1),
//! m.end(2)).

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static ADDRESS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(อยู่บ้านเลขที่|บ้านเลขที่|เลขที่|ที่อยู่|อยู่ที่|ส่งที่)[\s:：]*([^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))",
    )
    .expect("thai address regex")
});

pub struct ThaiAddressDetector;

impl Detector for ThaiAddressDetector {
    fn name(&self) -> &'static str {
        "thai_address"
    }
    fn entity_type(&self) -> &'static str {
        "thai_address"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in ADDRESS_RE.captures_iter(text).flatten() {
            let g1 = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let g2 = match caps.get(2) {
                Some(g) => g,
                None => continue,
            };
            let full = &text[g1.start()..g2.end()];
            let matched = full.trim();
            if matched.chars().count() < 6 {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, g1.start()),
                end: byte_to_char(text, g2.end()),
                matched_text: matched.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
