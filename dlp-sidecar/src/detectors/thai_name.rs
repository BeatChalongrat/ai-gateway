//! Thai personal name detector (title-prefix gated) — port of
//! ``thai_name.py``.
//!
//! Two patterns share the same body: Thai-prefix + Thai-name OR
//! English-prefix + Thai-name. Compound-noun false positives
//! (``นายก`` / ``นายพล`` / ``นางแบบ`` …) are filtered post-match.
//! ``seen_spans`` dedups between the two patterns when both could
//! match the same span.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

// Thai character class — matches Python ``[ก-ฺเ-๎]``.
// Two ranges: U+0E01–U+0E3A and U+0E40–U+0E4E.
const THAI_CHAR: &str = r"[\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}]";

const THAI_TITLE_PREFIXES: &str = concat!(
    r"นางสาว|น\.ส\.|",
    r"นาง|นาย|",
    r"เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.|",
    r"คุณ|",
    r"ศ\.|ผศ\.|รศ\.|ดร\.|",
    r"นพ\.|พญ\.|ทพ\.|ทพญ\.|ภก\.|ภญ\.|",
    r"พล\.อ\.|พล\.ท\.|พล\.ต\.|",
    r"ร\.ต\.อ\.|พ\.ต\.อ\.|",
    r"ว่าที่ร้อยตรี",
);

const ENGLISH_TITLE_PREFIXES: &str = r"Mr\.|Mrs\.|Ms\.|Miss|Dr\.";

static THAI_NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        r"(?:(?:{})\s*(?:{tc}{{2,}})\s+(?:{tc}{{2,}}))",
        THAI_TITLE_PREFIXES,
        tc = THAI_CHAR,
    );
    Regex::new(&pat).expect("thai name regex")
});

static MIXED_NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        r"(?:(?:{})\s*(?:{tc}{{2,}})\s+(?:{tc}{{2,}}))",
        ENGLISH_TITLE_PREFIXES,
        tc = THAI_CHAR,
    );
    Regex::new(&pat).expect("mixed name regex")
});

static FALSE_POSITIVE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"นายก(?:รัฐมนตรี|สมาคม|เทศมนตรี|สภา)|",
        r"นายพล|นายทหาร|นายตำรวจ|นายแพทย์|นายช่าง|นายหน้า|นายแบบ|",
        r"นางแบบ|นางงาม|นางฟ้า|นางเอก|นางพยาบาล",
    ))
    .expect("thai name fp")
});

pub struct ThaiNameDetector;

impl Detector for ThaiNameDetector {
    fn name(&self) -> &'static str {
        "thai_name"
    }
    fn entity_type(&self) -> &'static str {
        "thai_name"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();

        for re in [&*THAI_NAME_RE, &*MIXED_NAME_RE] {
            for m in re.find_iter(text).flatten() {
                let matched = m.as_str();
                // Python uses ``_FALSE_POSITIVE_RE.match(matched)`` —
                // anchored at start of the matched string. fancy-regex
                // ``is_match`` is unanchored, so we check that the FP
                // pattern matches starting at offset 0 of the match.
                let fp_hit = FALSE_POSITIVE_RE
                    .find(matched)
                    .ok()
                    .flatten()
                    .map(|x| x.start() == 0)
                    .unwrap_or(false);
                if fp_hit {
                    continue;
                }
                let s = byte_to_char(text, m.start());
                let e = byte_to_char(text, m.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: matched.to_string(),
                        confidence: 1.0,
                        priority: 0,
                    });
                }
            }
        }
        out
    }
}
