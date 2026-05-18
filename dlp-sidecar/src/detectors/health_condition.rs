//! Health condition detector — port of ``health_condition.py``.
//!
//! Two-stage: (1) labelled health regex with tempered-greedy value
//! capture + condition-value validation; (2) keyword regex with
//! personal-context window. Dedup via seen_spans across both
//! stages.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 150;

static KEYWORDS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ป่วยเป็น|เป็นโรค|ผ่าตัด|แพ้ยา|ผลเลือด|ผลตรวจ|",
        r"ตั้งครรภ์|แท้ง(?:ลูก)?|พิการ|จิตเวช|ซึมเศร้า|",
        r"ติดเชื้อ|วินิจฉัย|รักษา(?:ตัว)?|",
        r"เบาหวาน|มะเร็ง|ความดัน(?:สูง|ต่ำ)?|หัวใจ|ไต(?:วาย|เรื้อรัง)|",
        r"เอดส์|HIV|โควิด|วัณโรค|หอบหืด|ลมชัก|ไทรอยด์|",
        r"โรงพยาบาล(?:.{0,20})(?:ให้ยา|รักษา|ผ่าตัด|นอน|ตรวจ)|",
        r"(?:ผม|ฉัน|เขา|เธอ)(?:.{0,10})(?:โรค|ป่วย|ผ่าตัด|แพ้|เป็น)|",
        r"(?:I|he|she|they|my|his|her)\s+(?:have|has|had|got|was\s+diagnosed\s+with|suffer(?:s|ed)?\s+from|",
        r"tested\s+positive|am\s+(?:HIV|diabetic|pregnant|disabled))|",
        r"(?:my|his|her|their)\s+(?:diagnosis|condition|disease|treatment|prescription|disability|",
        r"medical\s+(?:record|history|condition)|mental\s+health|blood\s+test)|",
        r"(?:patient|diagnosed)\s+(?:with|as)|",
        r"(?:I|he|she|they)\s+(?:was|were)\s+diagnosed\s+with\s+\w+",
        r")",
    ))
    .expect("health keywords")
});

static PERSONAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|ลูก|พ่อ|แม่|สามี|ภรรยา|",
        r"ข้าพเจ้า|กระผม|หนู|คนไข้|ผู้ป่วย|",
        r"ปัจจุบัน|ตอนนี้|ขณะนี้|ล่าสุด|ผลตรวจ|ประวัติ|รายงาน|ข้อมูล|พนักงาน|สมาชิก|ลูกค้า|",
        r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|",
        r"\bpatient\b|\bclient\b|\bcurrently\b|\bemployee\b|\bmember\b|",
        r"\breport\b|\brecord\b|\bhistory\b",
        r")",
    ))
    .expect("health personal")
});

static LABELLED_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?P<label>",
        r"medical(?:\s+info)?|diagnosis|health\s+condition|disease|",
        r"\u{0e42}\u{0e23}\u{0e04}|",
        r"\u{0e1b}\u{0e23}\u{0e30}\u{0e27}\u{0e31}\u{0e15}\u{0e34}\u{0e2a}\u{0e38}\u{0e02}\u{0e20}\u{0e32}\u{0e1e}|",
        r"\u{0e2d}\u{0e32}\u{0e01}\u{0e32}\u{0e23}",
        r")\s*[:：]\s*",
        r"(?P<value>(?:(?![.;\n\r]|,\s*[A-Z][a-z]+\s*[:：])[^\n\r]){3,160})",
    ))
    .expect("health labelled")
});

static CONDITION_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"diabetes|blood\s+pressure|hypertension|asthma|cancer|depression|",
        r"HIV|covid|heart\s+disease|kidney\s+disease|pregnan|allerg|",
        r"\u{0e40}\u{0e1a}\u{0e32}\u{0e2b}\u{0e27}\u{0e32}\u{0e19}|",
        r"\u{0e04}\u{0e27}\u{0e32}\u{0e21}\u{0e14}\u{0e31}\u{0e19}|",
        r"\u{0e04}\u{0e27}\u{0e32}\u{0e21}\u{0e14}\u{0e31}\u{0e19}\u{0e2a}\u{0e39}\u{0e07}|",
        r"\u{0e2b}\u{0e2d}\u{0e1a}\u{0e2b}\u{0e37}\u{0e14}|",
        r"\u{0e21}\u{0e30}\u{0e40}\u{0e23}\u{0e47}\u{0e07}|",
        r"\u{0e0b}\u{0e36}\u{0e21}\u{0e40}\u{0e28}\u{0e23}\u{0e49}\u{0e32}",
        r")",
    ))
    .expect("condition value")
});

pub struct HealthConditionDetector;

impl Detector for HealthConditionDetector {
    fn name(&self) -> &'static str {
        "health_condition"
    }
    fn entity_type(&self) -> &'static str {
        "health_condition"
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

        // Stage 1 — labelled
        for caps in LABELLED_RE.captures_iter(text).flatten() {
            let val_m = match caps.name("value") {
                Some(g) => g,
                None => continue,
            };
            let raw = val_m.as_str();
            let trimmed = raw.trim();
            if !CONDITION_RE.is_match(trimmed).unwrap_or(false) {
                continue;
            }
            let leading_ws_chars = raw.chars().take_while(|c| c.is_whitespace()).count();
            let s_char = byte_to_char(text, val_m.start()) + leading_ws_chars;
            let e_char = s_char + trimmed.chars().count();
            seen.insert((s_char, e_char));
            out.push(RawMatch {
                start: s_char,
                end: e_char,
                matched_text: trimmed.to_string(),
                confidence: 1.0,
                priority: 0,
            });
        }

        // Stage 2 — keyword + personal-context window
        for m in KEYWORDS_RE.find_iter(text).flatten() {
            let s_char = byte_to_char(text, m.start());
            let e_char = byte_to_char(text, m.end());
            let lo = s_char.saturating_sub(CONTEXT_WINDOW);
            let hi = (e_char + CONTEXT_WINDOW).min(chars_total);
            let lo_b = char_to_byte(text, lo);
            let hi_b = char_to_byte(text, hi);
            let window = &text[lo_b..hi_b];
            if !PERSONAL_RE.is_match(window).unwrap_or(false) {
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
            });
        }

        out
    }
}
