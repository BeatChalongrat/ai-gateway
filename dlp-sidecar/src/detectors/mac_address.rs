//! MAC address detector — port of ``mac_address.py``.
//!
//! Regex: `\b([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})\b`
//! The Python detector reports start/end of GROUP 1 (which in this
//! pattern is the whole address). Post-match: drop broadcast + all-
//! zero placeholders.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static MAC_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})\b").expect("mac regex")
});

fn is_trivial(value: &str) -> bool {
    let normalized = value.replace('-', ":").to_lowercase();
    normalized == "00:00:00:00:00:00" || normalized == "ff:ff:ff:ff:ff:ff"
}

pub struct MacAddressDetector;

impl Detector for MacAddressDetector {
    fn name(&self) -> &'static str {
        "mac_address"
    }
    fn entity_type(&self) -> &'static str {
        "mac_address"
    }
    fn category(&self) -> &'static str {
        "network"
    }
    fn severity(&self) -> u32 {
        1  // LOW
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in MAC_RE.captures_iter(text).flatten() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let value = g.as_str();
            if is_trivial(value) {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, g.start()),
                end: byte_to_char(text, g.end()),
                matched_text: value.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
