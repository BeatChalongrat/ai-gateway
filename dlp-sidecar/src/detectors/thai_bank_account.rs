//! Thai bank account detector — port of ``thai_bank_account.py``.
//!
//! 10-12 digit account numbers with optional dash/space separators,
//! gated on a banking keyword within ±60 chars. **No** checksum —
//! Thai banks don't share a universal mod-N like the citizen ID
//! does, so the detector relies entirely on the keyword anchor.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 60;

const KEYWORDS: &[&str] = &[
    "บัญชี",
    "เลขบัญชี",
    "บัญชีเลขที่",
    "ธนาคาร",
    "account no",
    "account number",
    "bank account",
    "acc#",
    "acct",
];

static ACCT_RE: Lazy<Regex> = Lazy::new(|| {
    // 3 + 1 + 5 + 1..3 = 10..12 digits with optional separators.
    Regex::new(r"(?<!\d)(\d{3}[-\s]?\d[-\s]?\d{5}[-\s]?\d{1,3})(?!\d)")
        .expect("bank acct regex")
});

fn has_account_context(text: &str, char_start: usize, char_end: usize) -> bool {
    let total = text.chars().count();
    let lo = char_start.saturating_sub(CONTEXT_WINDOW);
    let hi = (char_end + CONTEXT_WINDOW).min(total);
    let lo_b = char_to_byte(text, lo);
    let hi_b = char_to_byte(text, hi);
    let window_lower = text[lo_b..hi_b].to_lowercase();
    KEYWORDS.iter().any(|kw| window_lower.contains(*kw))
}

pub struct ThaiBankAccountDetector;

impl Detector for ThaiBankAccountDetector {
    fn name(&self) -> &'static str {
        "thai_bank_account"
    }
    fn entity_type(&self) -> &'static str {
        "thai_bank_account"
    }
    fn category(&self) -> &'static str {
        "financial"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }
    fn priority(&self) -> i32 {
        10
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in ACCT_RE.captures_iter(text).flatten() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let raw = g.as_str();
            let digit_count = raw.chars().filter(|c| c.is_ascii_digit()).count();
            if !(10..=12).contains(&digit_count) {
                continue;
            }
            let s = byte_to_char(text, g.start());
            let e = byte_to_char(text, g.end());
            if !has_account_context(text, s, e) {
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
