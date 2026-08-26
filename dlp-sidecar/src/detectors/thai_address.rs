//! Thai address detector — port of ``thai_address.py``.
//!
//! Two-group regex (keyword + body). Span is start of group 1
//! through end of group 2 of the regex match (Python: m.start(1),
//! m.end(2)).
//!
//! Precision/recall stance (FIX23/FIX23b): fires only when the body
//! looks like a postal address (house number near the keyword AND an
//! address component or postcode). Known gaps accepted pending owner
//! review: building/condo/village/estate-only addresses, bare province
//! names, no house number, >30-char prose gap before the number.
//!
//! FIX23 — the body must (a) carry a digit within 30 chars of the
//! keyword (house number) AND (b) contain an address component on the
//! same line (ถนน/ซอย/หมู่/ตำบล/อำเภอ/จังหวัด/แขวง/เขต/กรุงเทพ/กทม or a
//! 5-digit postcode). Without those anchors, ordinary Thai prose
//! containing ``เลขที่`` ("the number that…") / ``อยู่ที่`` ("is at") /
//! ``ที่อยู่`` was swept to end-of-line as an "address" — the question
//! ``เลขที่ให้จำเมื่อกี้คืออะไร`` became ``<<ADDRESS_1>>`` on input (the
//! model echoed the token back), and the answer ``เลขที่ให้จำคือ 74-…``
//! was masked on output. Keep this regex byte-identical to Python.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static ADDRESS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(อยู่บ้านเลขที่|บ้านเลขที่|เลขที่|ที่อยู่|อยู่ที่|ส่งที่)[\s:：]*(?=[^\n\d]{0,30}\d)(?=[^\n]{0,200}?(?:ถนน|ถ\.|ซอย|ซ\.|หมู่|ม\.|ตำบล|ต\.|อำเภอ|อ\.|จังหวัด|จ\.|แขวง|เขต|กรุงเทพ|กทม|\d{5}(?=[\s,]|$)))([^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))",
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
