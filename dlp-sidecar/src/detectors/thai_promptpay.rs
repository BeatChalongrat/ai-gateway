//! Thai PromptPay detector — port of ``thai_promptpay.py``.
//!
//! Two-shape match: 10-digit mobile starting with 0, OR a 13-digit
//! national-ID-shaped run. ±80-char keyword gate is required for
//! either shape; the 13-digit branch also runs the Thai national
//! ID checksum (reuses :fn:`thai_national_id::is_valid_thai_id`).

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};
use crate::detectors::thai_national_id::is_valid_thai_id;

const CONTEXT_WINDOW: usize = 80;

const KEYWORDS: &[&str] = &["พร้อมเพย์", "พร้อมเพย์เบอร์", "promptpay"];

static PROMPTPAY_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?<!\d)(0\d{9}|\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)",
    )
    .expect("promptpay regex")
});

fn has_promptpay_context(text: &str, char_start: usize, char_end: usize) -> bool {
    let total = text.chars().count();
    let lo = char_start.saturating_sub(CONTEXT_WINDOW);
    let hi = (char_end + CONTEXT_WINDOW).min(total);
    let lo_b = char_to_byte(text, lo);
    let hi_b = char_to_byte(text, hi);
    let window_lower = text[lo_b..hi_b].to_lowercase();
    KEYWORDS.iter().any(|kw| window_lower.contains(*kw))
}

pub struct ThaiPromptPayDetector;

impl Detector for ThaiPromptPayDetector {
    fn name(&self) -> &'static str {
        "thai_promptpay"
    }
    fn entity_type(&self) -> &'static str {
        "thai_promptpay"
    }
    fn category(&self) -> &'static str {
        "financial"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }
    fn priority(&self) -> i32 {
        10
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in PROMPTPAY_RE.captures_iter(text).engine_ok() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let s = byte_to_char(text, g.start());
            let e = byte_to_char(text, g.end());
            if !has_promptpay_context(text, s, e) {
                continue;
            }
            let raw = g.as_str();
            let digits: String = raw.chars().filter(|c| c.is_ascii_digit()).collect();
            // 13-digit branch must also pass the Thai national ID
            // checksum — drops random 13-digit runs that happen to
            // sit near a "promptpay" mention.
            if digits.len() == 13 && !is_valid_thai_id(&digits) {
                continue;
            }
            out.push(RawMatch {
                start: s,
                end: e,
                matched_text: raw.to_string(),
                confidence: 1.0,
                priority: 10,
                entity_type: None,
            });
        }
        out
    }
}
