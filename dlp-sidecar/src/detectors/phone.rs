//! Phone-number detector — port of ``phone.py``.
//!
//! 3 branches (formatted / Thai-landline / bare-with-context) +
//! seen-spans dedup + sort by (start, end). The bare-digits branch
//! requires a phone-context keyword inside the ±25-char window.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk, EngineResult};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

static FORMATTED_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?\(?\d{2,4}\)?[\s.\-]\d{3,4}[\s.\-]\d{3,4}(?!\d)",
    )
    .expect("phone formatted")
});

// FIX27 Part 3 — a UUID's digit groups look like a formatted phone number;
// attachment envelopes carry file UUIDs on every turn. Mirrors phone.py _UUID_RE.
static UUID_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9A-Za-z])")
        .expect("uuid regex")
});

fn inside_uuid(spans: &[(usize, usize)], start_b: usize, end_b: usize) -> bool {
    spans.iter().any(|(s, e)| *s <= start_b && end_b <= *e)
}

static BARE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?<!\d)\+?\d{10,15}(?!\d)").expect("phone bare"));

// FIX38 C1 — Thai numerals. ``\d`` is Unicode-aware in this crate, so the
// regexes matched ``๐๘๑-๒๓๔-๕๖๗๘`` all along — but the digit COUNT below
// used ``is_ascii_digit`` and saw zero digits, so every formatted
// Thai-numeral number was silently dropped while Python kept it: the two
// engines disagreed on this shape until this round. The area-code class
// was ASCII-only in both engines (the 9-digit ``๐๒๗๗๘๘๙๙๐`` gap).
static THAI_LANDLINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?<!\d)([0๐][2-9๒-๙])[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)(?![A-Za-z])")
        .expect("phone thai landline")
});

/// A digit for phone purposes: ASCII ``0-9`` or Thai ``๐-๙``. Mirrors
/// Python's ``str.isdigit`` on the shapes users actually type.
fn is_phone_digit(c: char) -> bool {
    c.is_ascii_digit() || ('๐'..='๙').contains(&c)
}

/// ``000-000-0000`` / ``๐๐๐-๐๐๐-๐๐๐๐`` — an example, not a phone. Compared
/// per char (Thai digits are three bytes each, so a byte compare lies).
fn all_same_digit(digits: &str) -> bool {
    let mut it = digits.chars();
    match it.next() {
        Some(first) => it.all(|c| c == first),
        None => false,
    }
}

static PHONE_CONTEXT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?:call|phone|tel(?:ephone)?|mobile|cell|line\s*id|whatsapp|dial|contact\s+me|hotline|\+?66\s|โทร|มือถือ|เบอร์(?:\s*โทร)?|ติดต่อ|สายด่วน|หมายเลข(?:\s*โทร)?)",
    )
    .expect("phone context")
});

const CONTEXT_WINDOW: usize = 25;

pub struct PhoneDetector;

impl Detector for PhoneDetector {
    fn name(&self) -> &'static str {
        "phone"
    }
    fn entity_type(&self) -> &'static str {
        "phone_number"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }
    fn priority(&self) -> i32 {
        5
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        self.detect_ctx(text, false)
    }

    fn detect_ctx(&self, text: &str, from_decoded: bool) -> Vec<RawMatch> {
        let mut out: Vec<RawMatch> = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        let text_chars_count = text.chars().count();
        let uuid_spans: Vec<(usize, usize)> = UUID_RE
            .find_iter(text)
            .engine_ok()
            .map(|u| (u.start(), u.end()))
            .collect();

        // Branch 1 — formatted numbers, require ≥10 digits + reject
        // credit-card tail.
        for m in FORMATTED_RE.find_iter(text).engine_ok() {
            let span_text = m.as_str();
            let digit_count = span_text.chars().filter(|c| is_phone_digit(*c)).count();
            if digit_count < 10 {
                continue;
            }
            // Credit-card tail check: next 2 bytes after end == " "/"-"/"." + digit
            let end_b = m.end();
            let tail = &text.as_bytes()[end_b..(end_b + 2).min(text.len())];
            if tail.len() == 2
                && (tail[0] == b' ' || tail[0] == b'-' || tail[0] == b'.')
                && (b'0'..=b'9').contains(&tail[1])
            {
                continue;
            }
            // Reject trivial all-same-digit numbers (000-000-0000, …) —
            // mirrors phone.py _emit. Sample/example numbers pass through.
            let digits_only: String =
                span_text.chars().filter(|c| is_phone_digit(*c)).collect();
            if all_same_digit(&digits_only) {
                continue;
            }
            if inside_uuid(&uuid_spans, m.start(), m.end()) {
                continue; // FIX27: inside a UUID
            }
            let s = byte_to_char(text, m.start());
            let e = byte_to_char(text, m.end());
            if seen.insert((s, e)) {
                out.push(RawMatch {
                    start: s,
                    end: e,
                    matched_text: span_text.to_string(),
                    confidence: 1.0,
                    priority: 5,
                    entity_type: None,
                });
            }
        }

        // Branch 1b — Thai landlines
        for m in THAI_LANDLINE_RE.find_iter(text).engine_ok() {
            let span_text = m.as_str();
            let digits_only: String =
                span_text.chars().filter(|c| is_phone_digit(*c)).collect();
            if all_same_digit(&digits_only) {
                continue;
            }
            if inside_uuid(&uuid_spans, m.start(), m.end()) {
                continue; // FIX27: inside a UUID
            }
            let s = byte_to_char(text, m.start());
            let e = byte_to_char(text, m.end());
            if seen.insert((s, e)) {
                out.push(RawMatch {
                    start: s,
                    end: e,
                    matched_text: span_text.to_string(),
                    confidence: 1.0,
                    priority: 5,
                    entity_type: None,
                });
            }
        }

        // Branch 2 — bare digits. Require a phone-context keyword inside
        // the ±CONTEXT_WINDOW window, UNLESS from_decoded: base64-decoded
        // content is isolated, so the keyword gate is dropped — mirrors
        // phone.py's ``if from_decoded: _emit(...)`` relaxation. The
        // all-same-digit reject still applies in both modes.
        for m in BARE_RE.find_iter(text).engine_ok() {
            let s_char = byte_to_char(text, m.start());
            let e_char = byte_to_char(text, m.end());
            if seen.contains(&(s_char, e_char)) {
                continue;
            }
            let has_context = if from_decoded {
                true
            } else {
                let lo = s_char.saturating_sub(CONTEXT_WINDOW);
                let hi = (e_char + CONTEXT_WINDOW).min(text_chars_count);
                let lo_b = char_to_byte(text, lo);
                let hi_b = char_to_byte(text, hi);
                let window = &text[lo_b..hi_b];
                PHONE_CONTEXT_RE.is_match(window).engine_or(false)
            };
            if inside_uuid(&uuid_spans, m.start(), m.end()) {
                continue; // FIX27: inside a UUID
            }
            if has_context {
                let span_text = m.as_str();
                let digits_only: String =
                    span_text.chars().filter(|c| is_phone_digit(*c)).collect();
                if all_same_digit(&digits_only) {
                    continue;
                }
                seen.insert((s_char, e_char));
                out.push(RawMatch {
                    start: s_char,
                    end: e_char,
                    matched_text: span_text.to_string(),
                    confidence: 1.0,
                    priority: 5,
                    entity_type: None,
                });
            }
        }

        // Sort by (start, end) to match Python
        out.sort_by_key(|m| (m.start, m.end));
        out
    }
}

#[cfg(test)]
mod thai_digit_tests {
    //! FIX38 C1 — the shapes the FIX37 corpus caught leaking. Each shape is
    //! also in ``backend/evals/dlp/data/must_catch/phone.json`` so the
    //! Python/Rust parity gate covers it end to end.
    use super::PhoneDetector;
    use crate::detectors::base::Detector;

    fn hits(text: &str) -> Vec<String> {
        PhoneDetector
            .detect(text)
            .into_iter()
            .map(|m| m.matched_text)
            .collect()
    }

    #[test]
    fn formatted_thai_numerals_are_caught() {
        assert_eq!(hits("โทร ๐๘๑-๒๓๔-๕๖๗๘ นะ"), vec!["๐๘๑-๒๓๔-๕๖๗๘"]);
    }

    #[test]
    fn nine_digit_thai_numeral_landline_is_caught() {
        assert_eq!(hits("เบอร์ร้าน ๐๒๗๗๘๘๙๙๐ ช่วงนี้"), vec!["๐๒๗๗๘๘๙๙๐"]);
        assert_eq!(hits("ที่ติดต่อได้คือ ๐๙๔๕๕๖๖๗๗ ค่ะ"), vec!["๐๙๔๕๕๖๖๗๗"]);
    }

    #[test]
    fn bare_thai_numerals_still_need_a_keyword() {
        assert_eq!(hits("เบอร์เขาคือ ๐๘๑๒๒๒๓๓๔๔ ช่วยโทรที"), vec!["๐๘๑๒๒๒๓๓๔๔"]);
        assert!(hits("รหัสพัสดุ ๐๘๑๒๒๒๓๓๔๔ ส่งแล้ว").is_empty());
    }

    #[test]
    fn an_all_same_example_number_is_left_alone() {
        assert!(hits("ตัวอย่างเบอร์ ๐๐๐-๐๐๐-๐๐๐๐ ครับ").is_empty());
    }

    #[test]
    fn a_thai_year_or_amount_is_not_a_phone() {
        assert!(hits("ปี ๒๕๖๗ งบ ๑๒๓๔๕ บาท").is_empty());
    }
}
