//! Medical record number detector — port of ``medical_record.py``.
//!
//! Keyword-gated 6-10 digit numbers. Keyword regex uses ``\b`` word
//! boundaries (fancy-regex Unicode-aware) so Thai keywords match
//! correctly. The check filter keeps only keyword matches that
//! end ≤ number_start AND start ≥ number_start - 60.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 60;

static NUMBER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?<!\d)(\d{6,10})(?!\d)").expect("number regex"));

static KEYWORD_RE: Lazy<Regex> = Lazy::new(|| {
    // Python list — escaped + joined with ``|`` + wrapped in \b
    Regex::new(
        r"(?i)\b(?:mrn|medical\ record\ no|medical\ record\ number|patient\ id|patient\ no|patient\ number|hn|opd|เลข\ hn|หมายเลขผู้ป่วย|เลขที่ผู้ป่วย)\b",
    )
    .expect("keyword regex")
});

pub struct MedicalRecordDetector;

impl Detector for MedicalRecordDetector {
    fn name(&self) -> &'static str {
        "medical_record"
    }
    fn entity_type(&self) -> &'static str {
        "medical_record"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }
    fn priority(&self) -> i32 {
        10
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        // Pre-compute keyword byte-spans once so each number lookup
        // is O(k) over the keyword count, not O(N) over text length.
        let keyword_spans: Vec<(usize, usize)> = KEYWORD_RE
            .find_iter(text)
            .engine_ok()
            .map(|m| (m.start(), m.end()))
            .collect();

        let mut out = Vec::new();
        for caps in NUMBER_RE.captures_iter(text).engine_ok() {
            let g = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let num_start_b = g.start();
            // Convert window in BYTES: Python uses char offsets but
            // for ASCII keywords the two are equivalent. The Thai
            // keywords are matched by KEYWORD_RE against the same
            // byte-indexed text, so the byte-based filter is exact.
            let low_b = num_start_b.saturating_sub(CONTEXT_WINDOW * 3 + CONTEXT_WINDOW);
            // ^ generous byte radius: a 60-char Thai keyword window
            // could span up to 60 * 3 = 180 bytes. We over-estimate
            // and let the keyword-end ≤ num_start check do the
            // exact filtering.
            let _ = low_b;
            // Python's filter:
            //   keyword.end <= num_start AND keyword.start >= num_start - 60 (in chars)
            // We mirror it character-correct by re-checking each
            // keyword span's character distance to the number.
            let num_start_char = byte_to_char(text, num_start_b);

            let has_context = keyword_spans.iter().any(|&(ks, ke)| {
                if ke > num_start_b {
                    return false; // keyword must end before/at number
                }
                let ks_char = byte_to_char(text, ks);
                ks_char + CONTEXT_WINDOW >= num_start_char
            });

            if !has_context {
                continue;
            }

            out.push(RawMatch {
                start: num_start_char,
                end: byte_to_char(text, g.end()),
                matched_text: g.as_str().to_string(),
                confidence: 1.0,
                priority: 10,
                entity_type: None,
            });
        }
        out
    }
}
