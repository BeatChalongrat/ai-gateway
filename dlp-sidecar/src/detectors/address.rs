//! Labeled postal-address detector — port of ``address.py``.
//!
//! Two-stage: outer ``label: value`` capture, then inner check
//! that ``value`` contains an address-like sub-pattern. Span is
//! recomputed against the lstripped value so leading whitespace
//! inside the captured group doesn't bleed into the match.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk, EngineResult};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static ADDRESS_RE: Lazy<Regex> = Lazy::new(|| {
    let label = concat!(
        r"(?:",
        r"(?:home\s+|billing\s+|shipping\s+)?address",
        r"|(?:\u{0e17}\u{0e35}\u{0e48}\u{0e2d}\u{0e22}\u{0e39}\u{0e48}",
        r"(?:\u{0e08}\u{0e31}\u{0e14}\u{0e2a}\u{0e48}\u{0e07}|",
        r"\u{0e15}\u{0e32}\u{0e21}\u{0e1a}\u{0e31}\u{0e15}\u{0e23}\u{0e1b}\u{0e23}\u{0e30}\u{0e0a}\u{0e32}\u{0e0a}\u{0e19})?)",
        r")",
    );
    let pat = format!(r"(?i)(?P<label>{}){}", label, r"\s*[:：]\s*(?P<value>[^\n\r]{6,180})");
    Regex::new(&pat).expect("address regex")
});

static ADDRESS_LIKE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        concat!(
            r"(?i)(?:",
            r"\d{1,5}\s*/\s*\d{1,5}|\b\d{1,5}\b",
            r").{0,120}(?:",
            r"\b(?:road|rd\.?|street|st\.?|avenue|ave\.?|lane|soi|rama)\b",
            r"|\b(?:bangkok|huai\s+khwang|sukhumvit)\b",
            r"|[\u{0e01}-\u{0e5b}]*(?:\u{0e16}\u{0e19}\u{0e19}|\u{0e0b}\u{0e2d}\u{0e22}|\u{0e2a}\u{0e38}\u{0e02}\u{0e38}\u{0e21}\u{0e27}\u{0e34}\u{0e17}|\u{0e01}\u{0e23}\u{0e38}\u{0e07}\u{0e40}\u{0e17}\u{0e1e})",
            r"|\b\d{5}\b",
            r")",
        ),
    )
    .expect("address like")
});

pub struct AddressDetector;

impl Detector for AddressDetector {
    fn name(&self) -> &'static str {
        "address"
    }
    fn entity_type(&self) -> &'static str {
        "address"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in ADDRESS_RE.captures_iter(text).engine_ok() {
            let val_match = match caps.name("value") {
                Some(g) => g,
                None => continue,
            };
            let raw_val = val_match.as_str();
            let stripped = raw_val.trim();
            if !ADDRESS_LIKE_RE.is_match(stripped).engine_or(false) {
                continue;
            }

            // Python: start = value.start + leading-ws count (chars)
            //         end   = start + len(value.strip())  in chars
            let leading_ws_chars = raw_val.chars().take_while(|c| c.is_whitespace()).count();
            let s_char = byte_to_char(text, val_match.start()) + leading_ws_chars;
            let stripped_len_chars = stripped.chars().count();
            let e_char = s_char + stripped_len_chars;

            out.push(RawMatch {
                start: s_char,
                end: e_char,
                matched_text: stripped.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
