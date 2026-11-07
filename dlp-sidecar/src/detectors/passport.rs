//! Passport detector — port of ``passport.py``.
//!
//! Single regex with inline keyword + named ``value`` group. The
//! keyword set covers English (``passport``/``pp no``/``travel
//! document``) and Thai (``หนังสือเดินทาง``/``พาสปอร์ต``/
//! ``เลขหนังสือเดินทาง``). Numeric value shapes: 1-2 letters +
//! 6-9 digits, OR a bare 9-digit US passport.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static PASSPORT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:passport(?:\s*(?:number|no\.?))?",
        r"|เลขหนังสือเดินทาง",
        r"|หนังสือเดินทาง",
        r"|พาสปอร์ต",
        r"|travel\s*document",
        r"|pp\s*no)",
        r"(?:\s*(?:is|=|:|number|no\.?|#))*",
        r"\s*",
        r"(?P<value>[A-Z]{1,2}\d{6,9}|\d{9})",
    ))
    .expect("passport regex")
});

pub struct PassportDetector;

impl Detector for PassportDetector {
    fn name(&self) -> &'static str {
        "passport"
    }
    fn entity_type(&self) -> &'static str {
        "passport_number"
    }
    fn category(&self) -> &'static str {
        "identity"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in PASSPORT_RE.captures_iter(text).engine_ok() {
            let g = match caps.name("value") {
                Some(g) => g,
                None => continue,
            };
            out.push(RawMatch {
                start: byte_to_char(text, g.start()),
                end: byte_to_char(text, g.end()),
                matched_text: g.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
