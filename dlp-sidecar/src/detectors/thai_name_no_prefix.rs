//! Fallback Thai-name detector (no title prefix) — port of
//! ``thai_name_no_prefix.py``.
//!
//! Two-word Thai-script run + context keyword within ±25 chars
//! **outside** the match span. The cursor walk is load-bearing —
//! a rejected first match must NOT swallow downstream tokens, so on
//! a context-filter reject the cursor advances by exactly **one
//! codepoint**, not the full match length. Python does this with
//! ``pos = start + 1`` on str (codepoint indices); Rust does the
//! equivalent by stepping past the first char's UTF-8 byte length.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

// Same Thai-char class as thai_name.rs / thai_address. Two ranges.
const THAI_CHAR: &str = r"[\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}]";

// Context keywords — keep order identical to Python so any future
// regex alternation order changes stay obvious in the diff.
const CONTEXT_KEYWORDS: &[&str] = &[
    "ชื่อ",
    "ผู้ใช้",
    "ลูกค้า",
    "พนักงาน",
    "ผู้ส่ง",
    "เจ้าของ",
    "ลงชื่อ",
    "ผู้รับ",
];

static CONTEXT_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = CONTEXT_KEYWORDS.join("|");
    Regex::new(&pat).expect("name context regex")
});

static NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let kw_alt = CONTEXT_KEYWORDS.join("|");
    // Mirror the Python construction:
    //   (?<!THAI)(?!(?:KW)(?:\s|$|[^THAI]))(THAI{2,15}\s+THAI{2,15})(?!THAI)
    let pat = format!(
        r"(?<!{tc})(?!(?:{kw})(?:\s|$|[^\u{{0E01}}-\u{{0E3A}}\u{{0E40}}-\u{{0E4E}}]))({tc}{{2,15}}\s+{tc}{{2,15}})(?!{tc})",
        tc = THAI_CHAR,
        kw = kw_alt,
    );
    Regex::new(&pat).expect("name regex")
});

const CONTEXT_WINDOW_CHARS: usize = 25;

pub struct ThaiNameNoPrefixDetector;

impl Detector for ThaiNameNoPrefixDetector {
    fn name(&self) -> &'static str {
        "thai_name_no_prefix"
    }
    fn entity_type(&self) -> &'static str {
        "thai_name"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }
    fn priority(&self) -> i32 {
        -5
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out: Vec<RawMatch> = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        let total_chars = text.chars().count();

        // Byte cursor. Search from text[pos_byte..] each iteration.
        let mut pos_byte = 0usize;
        let n_bytes = text.len();

        while pos_byte < n_bytes {
            let remaining = &text[pos_byte..];
            // Search the remainder for the first match. Captures
            // group 1 is the two-word name.
            let captures = match NAME_RE.captures(remaining) {
                Ok(Some(c)) => c,
                _ => break,
            };
            let g1 = match captures.get(1) {
                Some(g) => g,
                None => break,
            };
            // Translate offsets back to absolute byte positions.
            let abs_start_b = pos_byte + g1.start();
            let abs_end_b = pos_byte + g1.end();

            // Context window in CHARACTERS, outside the match span.
            let abs_start_char = byte_to_char(text, abs_start_b);
            let abs_end_char = byte_to_char(text, abs_end_b);
            let low_char = abs_start_char.saturating_sub(CONTEXT_WINDOW_CHARS);
            let high_char = (abs_end_char + CONTEXT_WINDOW_CHARS).min(total_chars);

            // Translate char window to byte window.
            let low_b = crate::detectors::base::char_to_byte(text, low_char);
            let high_b = crate::detectors::base::char_to_byte(text, high_char);

            // Build the OUTSIDE slice: before + after.
            let mut outside = String::new();
            outside.push_str(&text[low_b..abs_start_b]);
            outside.push_str(&text[abs_end_b..high_b]);

            let context_ok = CONTEXT_RE.is_match(&outside).unwrap_or(false);

            if context_ok {
                if seen.insert((abs_start_char, abs_end_char)) {
                    out.push(RawMatch {
                        start: abs_start_char,
                        end: abs_end_char,
                        matched_text: g1.as_str().to_string(),
                        confidence: 1.0,
                        priority: -5,
                    });
                }
                pos_byte = abs_end_b;
            } else {
                // Step past ONE codepoint at the rejected match start
                // (Python ``pos = start + 1`` on str = 1 codepoint).
                let first_char_len = text[abs_start_b..]
                    .chars()
                    .next()
                    .map(|c| c.len_utf8())
                    .unwrap_or(1);
                pos_byte = abs_start_b + first_char_len;
            }
        }
        out
    }
}
