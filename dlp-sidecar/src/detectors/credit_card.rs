//! Credit-card detector — port of ``credit_card.py``.
//!
//! Regex matches 13-19 digit runs with optional dash/space separators
//! and applies a standard mod-10 Luhn checksum. ``is_valid_luhn`` is
//! exposed ``pub`` so it can be reused by future detectors that need
//! Luhn — same pattern as ``thai_national_id::is_valid_thai_id``.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static CARD_RE: Lazy<Regex> = Lazy::new(|| {
    // \b(?:\d[ -]?){12,18}\d\b — between 13 and 19 digits with
    // optional dash/space separators between them.
    Regex::new(r"\b(?:\d[ -]?){12,18}\d\b").expect("card regex")
});

/// Standard Luhn mod-10 check. ``digits`` must be ASCII digit chars
/// only — Python source does the same character-filter upstream
/// before calling.
pub fn is_valid_luhn(digits: &str) -> bool {
    let len = digits.len();
    if len < 13 || len > 19 {
        return false;
    }
    if !digits.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    let mut total: u32 = 0;
    let mut alt = false;
    // Walk from right to left so the rightmost digit is at i=0
    // and gets the "no-double" treatment (Python ``alt = False``
    // initial state).
    for b in digits.bytes().rev() {
        let mut n = (b - b'0') as u32;
        if alt {
            n *= 2;
            if n > 9 {
                n -= 9;
            }
        }
        total += n;
        alt = !alt;
    }
    total % 10 == 0
}

pub struct CreditCardDetector;

impl Detector for CreditCardDetector {
    fn name(&self) -> &'static str {
        "credit_card"
    }
    fn entity_type(&self) -> &'static str {
        "credit_card_number"
    }
    fn category(&self) -> &'static str {
        "financial"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in CARD_RE.find_iter(text).flatten() {
            let raw = m.as_str();
            let digits: String = raw.chars().filter(|c| c.is_ascii_digit()).collect();
            if digits.len() < 13 || digits.len() > 19 {
                continue;
            }
            if !is_valid_luhn(&digits) {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: raw.to_string(),
                confidence: 1.0,
                priority: 0,
            });
        }
        out
    }
}

#[cfg(test)]
mod luhn_tests {
    use super::is_valid_luhn;

    #[test]
    fn known_valid_card_numbers() {
        // Industry-standard test cards (zero issuer signal, public
        // Luhn-valid sequences). All should pass.
        for n in [
            "4111111111111111", // Visa test
            "5500005555555559", // Mastercard test
            "378282246310005",  // Amex test (15 digits)
            "6011111111111117", // Discover test
        ] {
            assert!(is_valid_luhn(n), "{} should pass Luhn", n);
        }
    }

    #[test]
    fn off_by_one_check_digit_fails() {
        // Flip the last digit; Luhn must catch every single-digit
        // typo at the rightmost position.
        assert!(!is_valid_luhn("4111111111111112"));
        assert!(!is_valid_luhn("5500005555555550"));
    }

    #[test]
    fn too_short_or_too_long_rejected() {
        assert!(!is_valid_luhn("411111111111")); // 12
        assert!(!is_valid_luhn("41111111111111111111")); // 20
    }

    #[test]
    fn empty_rejected() {
        assert!(!is_valid_luhn(""));
    }

    #[test]
    fn non_digit_rejected() {
        // Upstream filters non-digits; the function itself must
        // also fail closed if a stray char slips through.
        assert!(!is_valid_luhn("4111-1111-1111-1111"));
    }
}
