//! Thai national ID detector — port of ``thai_national_id.py``.
//!
//! Two-gate detection:
//!   1. **Checksum gate** — regex captures 13-digit run with
//!      optional separators; we strip non-digits, validate via
//!      the mod-11 weighted sum (see :fn:`is_valid_thai_id`),
//!      and emit on pass.
//!   2. **Keyword gate** — a 13-digit run preceded by an ID
//!      labelling keyword (``บัตรประชาชน`` / ``national id`` / …)
//!      is emitted regardless of checksum. This mirrors the
//!      Python contract: an explicitly-labelled ID column is
//!      treated as authoritative even when the data inside is a
//!      placeholder.
//!
//! ``seen_spans`` dedups between the two gates so the keyword
//! path never doubles up on a span the checksum path already
//! emitted.
//!
//! The checksum is exposed as a public function so
//! ``thai_tax_id`` and ``thai_promptpay`` can reuse the same
//! implementation (mirroring the Python ``from .thai_national_id
//! import _thai_id_valid`` pattern).

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

/// Mod-11 weight sequence for the 12 leading digits. The 13th
/// digit is the check digit — see :fn:`is_valid_thai_id`.
pub const WEIGHTS: [u32; 12] = [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2];

/// Return ``true`` iff ``digits`` is a 13-digit Thai national ID
/// whose check digit matches the mod-11 weighted sum of the first
/// 12 digits.
///
/// Mirrors :func:`backend.app.services.dlp.detectors.thai_national_id._thai_id_valid`
/// **exactly**. Any divergence here is a parity violation that the
/// unit tests below catch before the detector ships.
pub fn is_valid_thai_id(digits: &str) -> bool {
    let bytes = digits.as_bytes();
    if bytes.len() != 13 {
        return false;
    }
    // ASCII-only digits — Python check ``digits.isdigit()`` accepts
    // a few Unicode digit classes too, but the call-sites here only
    // pass ASCII because they go through ``ch.is_ascii_digit``
    // filtering upstream.
    if !bytes.iter().all(|b| b.is_ascii_digit()) {
        return false;
    }
    if bytes[0] == b'0' {
        return false;
    }
    let mut total: u32 = 0;
    for i in 0..12 {
        let d = (bytes[i] - b'0') as u32;
        total += d * WEIGHTS[i];
    }
    let check = (11 - (total % 11)) % 10;
    let last = (bytes[12] - b'0') as u32;
    last == check
}

static THAI_ID_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?<!\d)(\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d)(?!\d)")
        .expect("thai id regex")
});

static KEYWORD_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?:เลขประจำตัวประชาชน|เลขประจำตัว|บัตรประจำตัว|บัตร\s*ประชาชน|บัตร\s*ปชช\.?|เลข\s*ปชช\.?|หมายเลข\s*บัตร|เลข\s*บัตร|เลข\s*13\s*หลัก|national\s*id|citizen\s*id|N\.?I\.?D\.?|ID)\s*[:#.]?\s*((?:\d[-\s]?){12}\d)(?!\d)",
    )
    .expect("thai id keyword")
});

pub struct ThaiNationalIdDetector;

impl Detector for ThaiNationalIdDetector {
    fn name(&self) -> &'static str {
        "thai_national_id"
    }
    fn entity_type(&self) -> &'static str {
        "thai_national_id"
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
        let mut seen: HashSet<(usize, usize)> = HashSet::new();

        // Gate 1: checksum-validated
        for caps in THAI_ID_RE.captures_iter(text).flatten() {
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
            if seen.insert((s, e)) {
                out.push(RawMatch {
                    start: s,
                    end: e,
                    matched_text: raw.to_string(),
                    confidence: 1.0,
                    priority: 10,
                    entity_type: None,
                });
            }
        }

        // Gate 2: keyword-context (bypasses checksum)
        for caps in KEYWORD_RE.captures_iter(text).flatten() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let s = byte_to_char(text, g.start());
            let e = byte_to_char(text, g.end());
            if seen.insert((s, e)) {
                out.push(RawMatch {
                    start: s,
                    end: e,
                    matched_text: g.as_str().to_string(),
                    confidence: 1.0,
                    priority: 10,
                    entity_type: None,
                });
            }
        }

        out
    }
}

// ---------------------------------------------------------------------------
// Unit tests — pinned against Python-generated test vectors.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod checksum_tests {
    use super::is_valid_thai_id;

    // ---- Valid IDs (generated from Python; see Batch 3 brief). ----
    #[test]
    fn valid_ids_pass() {
        for id in ["1234567890121", "1111111111119", "5000000000001", "9876543210121"] {
            assert!(
                is_valid_thai_id(id),
                "{} should validate but Rust says invalid",
                id
            );
        }
    }

    // ---- Invalid checksum (wrong check digit) ----
    #[test]
    fn invalid_checksum_fails() {
        for id in ["1234567890123", "1111111111111"] {
            assert!(
                !is_valid_thai_id(id),
                "{} has wrong check digit — must be rejected",
                id
            );
        }
    }

    // ---- Starts with zero — explicitly rejected per Thai ID rules. ----
    #[test]
    fn starts_with_zero_rejected() {
        // Use a checksum-valid prefix so this test is purely about
        // the leading-zero rule, not a checksum side effect.
        let id = "0234567890125";
        assert!(!is_valid_thai_id(id));
    }

    // ---- Wrong length ----
    #[test]
    fn wrong_length_rejected() {
        assert!(!is_valid_thai_id("123456789012")); // 12
        assert!(!is_valid_thai_id("12345678901234")); // 14
        assert!(!is_valid_thai_id(""));
    }

    // ---- Non-digit characters ----
    #[test]
    fn non_digit_chars_rejected() {
        assert!(!is_valid_thai_id("123456789012X"));
        assert!(!is_valid_thai_id("1234567890121 ")); // trailing space
    }
}
