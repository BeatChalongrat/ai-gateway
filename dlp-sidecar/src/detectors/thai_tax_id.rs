//! Thai tax ID detector — port of ``thai_tax_id.py``.
//!
//! Same regex + mod-11 checksum as ``thai_national_id`` but with an
//! additional ±60-char keyword gate (in lowercased windows).
//!
//! Unlike ``thai_national_id``, this detector does NOT reject
//! leading-zero values: corporate tax IDs are issued in the
//! ``0xxxxxxxxxxxx`` range. The keyword gate is what disambiguates a
//! tax ID from any other 13-digit run that passes the checksum — so
//! we use a tax-specific :fn:`is_valid_thai_tax_id` rather than
//! ``thai_national_id::is_valid_thai_id`` (mirroring Python's separate
//! ``_thai_tax_id_valid``).

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};
use crate::detectors::thai_national_id::WEIGHTS;

const CONTEXT_WINDOW: usize = 60;

/// Window (chars) immediately before the digit run scanned for a
/// strong tax-ID label. Mirrors Python ``_STRONG_PREFIX_WINDOW``.
const STRONG_PREFIX_WINDOW: usize = 30;

/// Same mod-11 weighted checksum as the national ID, but corporate
/// tax IDs are issued in the ``0xxxxxxxxxxxx`` range so we do NOT
/// reject leading-zero values. Mirrors Python ``_thai_tax_id_valid``.
fn is_valid_thai_tax_id(digits: &str) -> bool {
    let bytes = digits.as_bytes();
    if bytes.len() != 13 {
        return false;
    }
    if !bytes.iter().all(|b| b.is_ascii_digit()) {
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

// Keywords match Python ``_TAX_KEYWORDS`` (14 entries). Substring-
// tested against the lowercased window so a single Unicode case is enough.
const KEYWORDS: &[&str] = &[
    "เลขประจำตัวผู้เสียภาษี",
    "เลขผู้เสียภาษี",
    "เลขภาษี",
    "ผู้เสียภาษี",
    "เลขทะเบียนนิติบุคคล",
    "เลขนิติบุคคล",
    "tax id",
    "tax id number",
    "tax number",
    "taxpayer id",
    "taxpayer number",
    "tin",
    "tax registration",
    "vat registration",
];

// Stronger gate matching Python ``_STRONG_TAX_LABELS``: when one of
// these explicit "this number IS a tax ID" labels sits immediately
// before the digit run (within STRONG_PREFIX_WINDOW chars), we relax
// the checksum gate — the only path that accepts checksum-invalid IDs
// (masked / typo'd / fake test values). These labels are Thai
// (caseless), so the prefix window is NOT lowercased — matching Python,
// which scans the raw ``text[low:start]`` slice.
const STRONG_TAX_LABELS: &[&str] = &[
    "เลขประจำตัวผู้เสียภาษี",
    "เลขผู้เสียภาษี",
    "เลขทะเบียนนิติบุคคล",
    "เลขนิติบุคคล",
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

/// True iff an explicit tax-ID label sits in the
/// ``STRONG_PREFIX_WINDOW`` chars immediately preceding ``char_start``.
/// Mirrors Python ``_has_strong_tax_prefix`` — scans the raw slice
/// ``text[low:start]`` (no lowercasing; labels are Thai/caseless).
fn has_strong_tax_prefix(text: &str, char_start: usize) -> bool {
    let lo = char_start.saturating_sub(STRONG_PREFIX_WINDOW);
    let lo_b = char_to_byte(text, lo);
    let start_b = char_to_byte(text, char_start);
    let window = &text[lo_b..start_b];
    STRONG_TAX_LABELS.iter().any(|kw| window.contains(*kw))
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
        for caps in NUMBER_RE.captures_iter(text).engine_ok() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let raw = g.as_str();
            let digits: String = raw.chars().filter(|c| c.is_ascii_digit()).collect();
            if digits.len() != 13 {
                continue;
            }
            let s = byte_to_char(text, g.start());
            let e = byte_to_char(text, g.end());
            if is_valid_thai_tax_id(&digits) {
                // Checksum-valid: require a tax keyword in the ±60-char window.
                if !has_tax_context(text, s, e) {
                    continue;
                }
            } else {
                // Checksum-invalid: only fire if a strong tax-ID label sits
                // in the 30 chars immediately before the number.
                if !has_strong_tax_prefix(text, s) {
                    continue;
                }
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
