//! Religion/belief detector — port of ``religion_belief.py``.
//!
//! Keyword regex + personal-context window + _merge_adjacent. The
//! merge step combines overlapping/adjacent spans into single
//! results so an English "I am a practicing Buddhist" emits one
//! match instead of two from overlapping alternations.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

const CONTEXT_WINDOW: usize = 150;

static KEYWORDS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        // Thai
        r"นับถือ|ศาสนา|ศรัทธา|ละหมาด|สวดมนต์|",
        r"เข้าวัด|ถือศีล|บวช|มุสลิม|อิสลาม|พุทธ|คริสต์|ฮินดู|ซิกข์|",
        r"ยิว|ยูดาย์|",
        // English
        r"\b(?:my|his|her|their)\s+(?:religion|faith|belief)\b|",
        r"\b(?:I|he|she|they)\s+(?:am|is|are|was|were)\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)\b|",
        r"\bI'm\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)\b|",
        r"\b(?:I|he|she|they)\s+(?:believes?|worships?|prays?|practices?)\b|",
        r"\b(?:I|he|she|they)\s+(?:attends?|attended|goe?s?\s+to|went\s+to|visits?|visited)\s+(?:the\s+)?(?:local\s+)?(?:mosque|church|synagogue|temple|gurudwara|chapel|cathedral)\b|",
        r"\b(?:practicing|devout|observant)\s+(?:Muslim|Christian|Buddhist|Hindu|Sikh|Jewish|Catholic|Protestant)\b|",
        r"\b(?:I|he|she|they)\s+(?:converted?\s+to|follows?)\b",
        r")",
    ))
    .expect("religion keywords")
});

static PERSONAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)(?:",
        r"ผม|ฉัน|ดิฉัน|เขา|เธอ|พี่|น้อง|ลูก|พ่อ|แม่|สามี|ภรรยา|",
        r"ข้าพเจ้า|กระผม|หนู|",
        r"\bI\b|\bmy\b|\bme\b|\bhis\b|\bher\b|\bhe\b|\bshe\b|\bthey\b|\btheir\b",
        r")",
    ))
    .expect("religion personal")
});

pub struct ReligionBeliefDetector;

impl Detector for ReligionBeliefDetector {
    fn name(&self) -> &'static str {
        "religion_belief"
    }
    fn entity_type(&self) -> &'static str {
        "religion_belief"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let chars_total = text.chars().count();
        let mut found: Vec<RawMatch> = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();

        for m in KEYWORDS_RE.find_iter(text).flatten() {
            let s_char = byte_to_char(text, m.start());
            let e_char = byte_to_char(text, m.end());

            // Personal-context window in CHARACTERS
            let lo = s_char.saturating_sub(CONTEXT_WINDOW);
            let hi = (e_char + CONTEXT_WINDOW).min(chars_total);
            let lo_b = crate::detectors::base::char_to_byte(text, lo);
            let hi_b = crate::detectors::base::char_to_byte(text, hi);
            let window = &text[lo_b..hi_b];
            if !PERSONAL_RE.is_match(window).unwrap_or(false) {
                continue;
            }
            if !seen.insert((s_char, e_char)) {
                continue;
            }
            found.push(RawMatch {
                start: s_char,
                end: e_char,
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }

        merge_adjacent(found, text)
    }
}

fn merge_adjacent(mut matches: Vec<RawMatch>, text: &str) -> Vec<RawMatch> {
    if matches.len() <= 1 {
        return matches;
    }
    matches.sort_by_key(|m| (m.start, m.end));
    let mut merged: Vec<RawMatch> = vec![matches.remove(0)];
    for m in matches {
        let prev_idx = merged.len() - 1;
        let prev = &merged[prev_idx];
        if m.start <= prev.end {
            // Recompute the merged matched_text from text by char-slice.
            let new_end = prev.end.max(m.end);
            let chars: Vec<char> = text.chars().collect();
            let span: String = chars[prev.start..new_end].iter().collect();
            merged[prev_idx] = RawMatch {
                start: prev.start,
                end: new_end,
                matched_text: span,
                confidence: 1.0,
                priority: prev.priority.max(m.priority),
                entity_type: None,
            };
        } else {
            merged.push(m);
        }
    }
    merged
}
