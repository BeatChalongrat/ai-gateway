//! IBAN detector — port of ``iban.py``.
//!
//! Regex (verbatim): `\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b`.
//! Post-match: normalise (strip spaces, upper), check length
//! 15..=34, then run ISO 13616 mod-97 — keep only if residue == 1.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static IBAN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b").expect("iban regex")
});

/// ISO 13616 mod-97 residue. Translation rule:
///   * digits stay as-is
///   * letters: A=10, B=11, ..., Z=35
/// We feed the rearranged string (tail || head) into the residue
/// machine, chunking 9 digits at a time to stay inside ``u64``.
fn mod97(normalized: &str) -> Option<u64> {
    // rearrange: move first 4 chars to the end
    if normalized.len() < 4 {
        return None;
    }
    let bytes = normalized.as_bytes();
    let head = &bytes[..4];
    let tail = &bytes[4..];

    let mut digits = String::with_capacity(normalized.len() * 2);
    for ch in tail.iter().chain(head.iter()) {
        let c = *ch as char;
        if c.is_ascii_digit() {
            digits.push(c);
        } else if c.is_ascii_alphabetic() {
            // 'A' as char = 65, want 10 → ord - 55
            let v = (c as u32) - 55;
            digits.push_str(&v.to_string());
        } else {
            return None;
        }
    }

    // Process in chunks to avoid huge int conversion (Python does
    // the same — see iban.py:35-38).
    let mut residue: u64 = 0;
    let mut i = 0;
    while i < digits.len() {
        let end = std::cmp::min(i + 9, digits.len());
        let chunk = &digits[i..end];
        let combined = format!("{}{}", residue, chunk);
        residue = combined.parse::<u64>().ok()? % 97;
        i = end;
    }
    Some(residue)
}

pub struct IbanDetector;

impl Detector for IbanDetector {
    fn name(&self) -> &'static str {
        "iban"
    }
    fn entity_type(&self) -> &'static str {
        "iban"
    }
    fn category(&self) -> &'static str {
        "financial"
    }
    fn severity(&self) -> u32 {
        3  // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in IBAN_RE.find_iter(text).flatten() {
            let raw = m.as_str();
            let normalized: String = raw.chars().filter(|c| *c != ' ').collect::<String>().to_uppercase();
            if normalized.len() < 15 || normalized.len() > 34 {
                continue;
            }
            // Country code is two letters, check digits two ascii digits
            if !normalized[..2].chars().all(|c| c.is_ascii_alphabetic()) {
                continue;
            }
            if !normalized[2..4].chars().all(|c| c.is_ascii_digit()) {
                continue;
            }
            match mod97(&normalized) {
                Some(1) => {}
                _ => continue,
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
