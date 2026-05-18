//! Labeled salary / income detector — port of ``income.py``.
//!
//! Label + value (+ optional unit + per-month tail). Span is
//! from value.start to overall match end, then re-stripped.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static INCOME_RE: Lazy<Regex> = Lazy::new(|| {
    let label = concat!(
        r"(?:",
        r"salary|income|monthly\s+income|compensation",
        r"|\u{0e23}\u{0e32}\u{0e22}\u{0e44}\u{0e14}\u{0e49}",
        r"|\u{0e40}\u{0e07}\u{0e34}\u{0e19}\u{0e40}\u{0e14}\u{0e37}\u{0e2d}\u{0e19}",
        r"|\u{0e23}\u{0e32}\u{0e22}\u{0e23}\u{0e31}\u{0e1a}\u{0e15}\u{0e48}\u{0e2d}\u{0e40}\u{0e14}\u{0e37}\u{0e2d}\u{0e19}",
        r")",
    );
    let pat = format!(
        r"(?i)(?P<label>{}){}",
        label,
        concat!(
            r"\s*[:：]?\s*",
            r"(?P<value>(?:THB\s*)?\d{2,3}(?:,\d{3})+|\d{4,9})",
            r"\s*(?:THB|baht|\u{0e1a}\u{0e32}\u{0e17})?",
            r"(?:\s*(?:/|per\s+)?(?:month|monthly|\u{0e40}\u{0e14}\u{0e37}\u{0e2d}\u{0e19}))?",
        )
    );
    Regex::new(&pat).expect("income regex")
});

pub struct IncomeDetector;

impl Detector for IncomeDetector {
    fn name(&self) -> &'static str {
        "income"
    }
    fn entity_type(&self) -> &'static str {
        "income"
    }
    fn category(&self) -> &'static str {
        "financial"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in INCOME_RE.captures_iter(text).flatten() {
            let val_m = match caps.name("value") {
                Some(g) => g,
                None => continue,
            };
            // Python: start = value.start, end = overall match end
            let overall = match caps.get(0) {
                Some(g) => g,
                None => continue,
            };
            let s_b = val_m.start();
            let e_b = overall.end();
            let raw = &text[s_b..e_b];
            let trimmed = raw.trim();
            out.push(RawMatch {
                start: byte_to_char(text, s_b),
                end: byte_to_char(text, e_b),
                matched_text: trimmed.to_string(),
                confidence: 1.0,
                priority: 0,
            });
        }
        out
    }
}
