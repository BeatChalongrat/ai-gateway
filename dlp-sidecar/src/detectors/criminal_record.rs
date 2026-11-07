//! Criminal record detector — port of ``criminal_record.py``.
//!
//! Keyword regex + personal-context window. No merge step.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk, EngineResult};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 150;

static KEYWORDS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ถูกจับ|ต้องหา|จำคุก|ติดคุก|รอลงอาญา|คุมประพฤติ|ถูกฟ้อง|",
        r"คดี(?:ลัก|ฉ้อ|ยา|ข่มขืน|ฆ่า|ทำร้าย|อาญา|แพ่ง)|",
        r"ประวัติอาชญากรรม|ประวัติคดี|ประวัติต้องหา|",
        r"(?:ผู้ต้องหา|จำเลย|นักโทษ)(?:.{0,20})(?:ชื่อ|คือ|นาย|นาง)|",
        r"(?:พิพากษา|ตัดสิน|ลงโทษ)(?:.{0,30})(?:จำคุก|ปรับ|รอลงอาญา|คุมประพฤติ)|",
        r"(?:I|he|she|they)\s+(?:was|were|got|been)\s+(?:arrested|convicted|sentenced|charged|incarcerated)|",
        r"(?:my|his|her|their)\s+(?:criminal\s+record|conviction|arrest|sentence|felony|parole|probation)|",
        r"(?:I|he|she|they)\s+(?:served|spent)\s+(?:time|years?|months?)\s+(?:in\s+(?:prison|jail))|",
        r"(?:criminal\s+record|arrest\s+record|police\s+record)\s+(?:of|for|shows)|",
        r"(?:convicted|sentenced)\s+(?:of|to|for)\s+\w+",
        r")",
    ))
    .expect("criminal keywords")
});

static PERSONAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|นาย|นาง|น\.ส\.|",
        r"ข้าพเจ้า|กระผม|ผู้ต้องหา|จำเลย|",
        r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|",
        r"\bdefendant\b|\bsuspect\b",
        r")",
    ))
    .expect("criminal personal")
});

pub struct CriminalRecordDetector;

impl Detector for CriminalRecordDetector {
    fn name(&self) -> &'static str {
        "criminal_record"
    }
    fn entity_type(&self) -> &'static str {
        "criminal_record"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let chars_total = text.chars().count();
        let mut out = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        for m in KEYWORDS_RE.find_iter(text).engine_ok() {
            let s_char = byte_to_char(text, m.start());
            let e_char = byte_to_char(text, m.end());
            let lo = s_char.saturating_sub(CONTEXT_WINDOW);
            let hi = (e_char + CONTEXT_WINDOW).min(chars_total);
            let lo_b = char_to_byte(text, lo);
            let hi_b = char_to_byte(text, hi);
            let window = &text[lo_b..hi_b];
            if !PERSONAL_RE.is_match(window).engine_or(false) {
                continue;
            }
            if !seen.insert((s_char, e_char)) {
                continue;
            }
            out.push(RawMatch {
                start: s_char,
                end: e_char,
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
