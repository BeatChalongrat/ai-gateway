//! US Social Security Number detector — port of ``ssn.py``.
//!
//! Python regex: `\b(\d{3})-(\d{2})-(\d{4})\b` with post-match
//! validation per SSA rules:
//!   * area (group 1) != "000", != "666", first digit != "9"
//!   * group (group 2) != "00"
//!   * serial (group 3) != "0000"

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static SSN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b(\d{3})-(\d{2})-(\d{4})\b").expect("ssn regex")
});

pub struct SsnDetector;

impl Detector for SsnDetector {
    fn name(&self) -> &'static str {
        "ssn"
    }
    fn entity_type(&self) -> &'static str {
        "us_ssn"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3  // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in SSN_RE.captures_iter(text).engine_ok() {
            let full = match caps.get(0) {
                Some(g) => g,
                None => continue,
            };
            let area = match caps.get(1) {
                Some(g) => g.as_str(),
                None => continue,
            };
            let group = match caps.get(2) {
                Some(g) => g.as_str(),
                None => continue,
            };
            let serial = match caps.get(3) {
                Some(g) => g.as_str(),
                None => continue,
            };

            if area == "000" || area == "666" || area.starts_with('9') {
                continue;
            }
            if group == "00" {
                continue;
            }
            if serial == "0000" {
                continue;
            }

            out.push(RawMatch {
                start: byte_to_char(text, full.start()),
                end: byte_to_char(text, full.end()),
                matched_text: full.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
