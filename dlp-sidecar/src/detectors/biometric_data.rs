//! Biometric data detector — port of ``biometric_data.py``.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk, EngineResult};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, char_to_byte, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 150;

static KEYWORDS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ลายนิ้วมือ|สแกนนิ้ว|สแกนใบหน้า|จดจำใบหน้า|",
        r"ม่านตา|สแกนม่านตา|เสียง(?:ของ)?(?:ผม|ฉัน|เขา|เธอ)|",
        r"ข้อมูลชีวภาพ|ไบโอเมตริก|",
        r"fingerprint|face\s*(?:scan|recognition|ID|detection|unlock)|",
        r"retina\s*scan|iris\s*scan|voiceprint|voice\s*recognition|",
        r"biometric\s*(?:data|scan|identifier|authentication|template)|",
        r"touch\s*ID|face\s*ID|palm\s*(?:print|scan)",
        r")",
    ))
    .expect("biometric keywords")
});

static PERSONAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|",
        r"ข้าพเจ้า|กระผม|หนู|ของ|ลงทะเบียน|ใช้|สแกน|",
        r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b|",
        r"\buse\b|\bregister|\bscan|\benroll|\bstore|\brecord",
        r")",
    ))
    .expect("biometric personal")
});

pub struct BiometricDataDetector;

impl Detector for BiometricDataDetector {
    fn name(&self) -> &'static str {
        "biometric_data"
    }
    fn entity_type(&self) -> &'static str {
        "biometric_data"
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
