//! Phone-number detector — port of ``phone.py``.
//!
//! 3 branches (formatted / Thai-landline / bare-with-context) +
//! seen-spans dedup + sort by (start, end). The bare-digits branch
//! requires a phone-context keyword inside the ±25-char window.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

static FORMATTED_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?\(?\d{2,4}\)?[\s.\-]\d{3,4}[\s.\-]\d{3,4}(?!\d)",
    )
    .expect("phone formatted")
});

static BARE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?<!\d)\+?\d{10,15}(?!\d)").expect("phone bare"));

static THAI_LANDLINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?<!\d)(0[2-9])[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)(?![A-Za-z])")
        .expect("phone thai landline")
});

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

        // Branch 1 — formatted numbers, require ≥10 digits + reject
        // credit-card tail.
        for m in FORMATTED_RE.find_iter(text).flatten() {
            let span_text = m.as_str();
            let digit_count = span_text.chars().filter(|c| c.is_ascii_digit()).count();
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
                span_text.chars().filter(|c| c.is_ascii_digit()).collect();
            if !digits_only.is_empty()
                && digits_only.bytes().all(|b| b == digits_only.as_bytes()[0])
            {
                continue;
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
        for m in THAI_LANDLINE_RE.find_iter(text).flatten() {
            let span_text = m.as_str();
            let digits_only: String =
                span_text.chars().filter(|c| c.is_ascii_digit()).collect();
            if !digits_only.is_empty()
                && digits_only.bytes().all(|b| b == digits_only.as_bytes()[0])
            {
                continue;
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
        for m in BARE_RE.find_iter(text).flatten() {
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
                PHONE_CONTEXT_RE.is_match(window).unwrap_or(false)
            };
            if has_context {
                let span_text = m.as_str();
                let digits_only: String =
                    span_text.chars().filter(|c| c.is_ascii_digit()).collect();
                if !digits_only.is_empty()
                    && digits_only.bytes().all(|b| b == digits_only.as_bytes()[0])
                {
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
