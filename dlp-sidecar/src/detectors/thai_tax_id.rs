//! Thai tax ID detector — port of ``thai_tax_id.py``.
//!
//! Same regex + checksum as ``thai_national_id`` but with an
//! additional ±60-char keyword gate (in lowercased windows). Reuses
//! :fn:`thai_national_id::is_valid_thai_id` so the two detectors
//! can never drift apart on the checksum.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};
use crate::detectors::thai_national_id::is_valid_thai_id;

const CONTEXT_WINDOW: usize = 60;

// Keywords match Python ``_TAX_KEYWORDS``. Substring-tested
// against the lowercased window so a single Unicode case is enough.
const KEYWORDS: &[&str] = &[
    "เลขประจำตัวผู้เสียภาษี",
    "เลขภาษี",
    "ผู้เสียภาษี",
    "tax id",
    "taxpayer id",
    "tin",
    "tax number",
];

static NUMBER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?<!\d)(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)")
        .expect("tax id regex")
});

fn has_tax_context(text: &str, char_start: usize, char_end: usize) -> bool {
    let total = text.chars().count();
    let lo = char_start.saturating_sub(CONTEXT_WINDOW);
    let hi = (char_end + CONTEXT_WINDOW).min(total);
    let lo_b = char_to_byte(text, lo);
    let hi_b = char_to_byte(text, hi);
    let window_lower = text[lo_b..hi_b].to_lowercase();
    KEYWORDS.iter().any(|kw| window_lower.contains(*kw))
}

pub struct ThaiTaxIdDetector;

impl Detector for ThaiTaxIdDetector {
    fn name(&self) -> &'static str {
        "thai_tax_id"
    }
    fn entity_type(&self) -> &'static str {
        "thai_tax_id"
    }
    fn category(&self) -> &'static str {
        "identity"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }
    fn priority(&self) -> i32 {
        10
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in NUMBER_RE.captures_iter(text).flatten() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let raw = g.as_str();
            let digits: String = raw.chars().filter(|c| c.is_ascii_digit()).collect();
            if !is_valid_thai_id(&digits) {
                continue;
            }
            let s = byte_to_char(text, g.start());
            let e = byte_to_char(text, g.end());
            if !has_tax_context(text, s, e) {
                continue;
            }
            out.push(RawMatch {
                start: s,
                end: e,
                matched_text: raw.to_string(),
                confidence: 1.0,
                priority: 10,
            });
        }
        out
    }
}
