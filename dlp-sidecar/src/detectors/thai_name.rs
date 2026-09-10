//! Thai personal name detector (title-prefix gated) — port of
//! ``thai_name.py``.
//!
//! FIX26 §1.2 brought this port to FULL parity with the Python
//! detector. The previous port (E0-era) required a surname, knew a
//! shorter title list and carried a 7-entry false-positive guard, so
//! the LIVE engine masked ``คุณคือใคร ชื่ออะไร`` (Python did not) and
//! missed ``คุณสมชาย`` with no surname (Python masked). Now:
//!   * compound titles are ordered before their short forms;
//!   * the surname is OPTIONAL;
//!   * a trailing word that is not a surname (``จำนวน`` / ``เบอร์`` …)
//!     is trimmed off the span;
//!   * the whole ``คุณ`` + function-word / particle guard is here.
//! Two patterns share the body: Thai-prefix + name OR English-prefix +
//! name. ``seen`` dedups between them.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk, EngineResult};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};
use crate::detectors::thai_gazetteer;

// Thai character class — matches Python ``[ก-ฺเ-๎]``.
const THAI_CHAR: &str = r"[\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}]";

// Ordering matters: every compound form must appear BEFORE its shorter
// prefix (alternation is first-match). Identical order to Python.
const THAI_TITLE_PREFIXES: &str = concat!(
    r"นางสาว|น\.ส\.|",
    r"หม่อมหลวง|หม่อมราชวงศ์|หม่อมเจ้า|",
    r"ว่าที่ร้อยตรี|",
    r"นาง|นาย|",
    r"เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.|",
    r"คุณ|",
    r"ศ\.ดร\.|ผศ\.ดร\.|รศ\.ดร\.|",
    r"ศ\.|ผศ\.|รศ\.|ดร\.|",
    r"นพ\.|พญ\.|ทพ\.|ทพญ\.|ภก\.|ภญ\.|",
    r"พล\.ต\.อ\.|พล\.ต\.ท\.|พล\.ต\.ต\.|",
    r"พล\.อ\.|พล\.ท\.|พล\.ต\.|",
    r"พ\.ต\.ท\.|พ\.ต\.ต\.|ร\.ต\.ท\.|ร\.ต\.ต\.|",
    r"จ\.ส\.ต\.|ส\.ต\.ท\.|ส\.ต\.ต\.|",
    r"ร\.ต\.อ\.|พ\.ต\.อ\.|",
    r"ม\.ล\.|ม\.ร\.ว\.|ม\.จ\.",
);

const ENGLISH_TITLE_PREFIXES: &str = r"Mr\.|Mrs\.|Ms\.|Miss|Dr\.";

static THAI_NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        r"(?:{})\s*(?P<given>{tc}{{2,}})(?P<surname>\s+{tc}{{2,}})?",
        THAI_TITLE_PREFIXES,
        tc = THAI_CHAR,
    );
    Regex::new(&pat).expect("thai name regex")
});

static MIXED_NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        r"(?:{})\s*(?P<given>{tc}{{2,}})(?P<surname>\s+{tc}{{2,}})?",
        ENGLISH_TITLE_PREFIXES,
        tc = THAI_CHAR,
    );
    Regex::new(&pat).expect("mixed name regex")
});

// Whole-match rejects, anchored at the START of the matched span —
// identical alternation to Python's ``_FALSE_POSITIVE_RE``. SARA AM
// caveat: on NFKC-normalised text ``ำ`` is ``ํ``+``า``, so every word
// containing it accepts both spellings.
static FALSE_POSITIVE_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        concat!(
            r"นายก(?:รัฐมนตรี|สมาคม|เทศมนตรี|สภา)|",
            r"นายพล|นายทหาร|นายตำรวจ|นายแพทย์|นายช่าง|นายหน้า|นายแบบ|",
            r"นายจ้าง|นายพราน|นายอำเภอ|นายด่าน|นายทุน|นายประกัน|นายท้าย|",
            r"นางแบบ|นางงาม|นางฟ้า|นางเอก|นางพยาบาล|นางรำ|นางสนม|",
            r"คุณ(?:ภาพ|ค่า|สมบัติ|ธรรม|ประโยชน์|วุฒิ|ลักษณะ){tc}*|",
            r"คุณ(?:พ่อ|แม่|หมอ|ครู|นาย|ยาย|ตา|ป้า|น้า|อา|ลุง|พี่|น้อง|หนู)|",
            r"คุณ(?:มาก|นะ|ครับ|ค่ะ|คะ|จ้า|จ๊ะ|จ้ะ|ด้วย|เลย|ก็|ที่|กับ|ของ|และ|หรือ|ใน|",
            r"จาก|ถึง|เอง|ทั้ง|ทุก|ส(?:ำ|ํา)หรับ|จริง|หลาย|อีก|แล้ว|เท่า|ล่วงหน้า|ๆ)|",
            r"คุณ(?:ท(?:ำ|ํา)|เป็น|จะ|ช่วย|อยาก|คือ|ชอบ|คิด|พูด|รู้|เข้าใจ|ได้|",
            r"ต้องการ|ต้อง|ควร|ก(?:ำ|ํา)ลัง|ไม่|อยู่|ใช้|เคย|สามารถ|ให้|บอก|ถาม|",
            r"ลอง|กรุณา|โปรด|เห็น|ตอบ|ส่ง|หมายถึง|",
            r"ว่า(?:ยังไง|อย่างไร|ไง|อะไร|จะ|มัน|เรา|ผม|ฉัน|ดี)|",
            r"มี(?:ความ|อะไร|ใคร|กี่|ข้อ|ค(?:ำ|ํา)|วิธี|เวลา|ปัญหา|หน้าที่)|",
            r"จ(?:ำ|ํา)(?:ได้|ไม่|อะไร|ฉัน|ผม|เรา|ชื่อ))",
        ),
        tc = THAI_CHAR,
    );
    Regex::new(&pat).expect("thai name fp")
});

// Words that can trail a name but are NOT surnames — trim the span back
// to the given name. Same set as Python's ``_NOT_SURNAME`` (with both
// SARA AM spellings).
const NOT_SURNAME: &[&str] = &[
    "จำนวน", "ที่", "ได้", "มี", "เป็น", "คือ", "และ", "หรือ", "ว่า",
    "ไป", "มา", "ให้", "กับ", "ของ", "จาก", "ถึง", "โดย", "เมื่อ",
    "แล้ว", "อยู่", "จะ", "ต้อง", "ควร", "อาจ", "น่า", "ยัง",
    "ทั้ง", "ทุก", "บาง", "แต่ละ", "เงินเดือน", "โบนัส", "เบอร์",
    "อีเมล", "ที่อยู่", "บัญชี", "โทร", "ป่วย", "ถูก", "โดน",
    "จํานวน", "ตําแหน่ง", "ตำแหน่ง", "แผนก", "อายุ", "วันเกิด", "เลขบัตร",
];

/// FIX40 — a surname is a WORD, not a clause. ``given`` matches a whole
/// Thai RUN (Thai has no spaces), and FIX29's ``trim_run`` guard only ran
/// in the `!has_surname` branch, so one space later in the sentence skipped
/// it and the match swallowed the rest of the line. Mirrors Python's
/// ``MAX_SURNAME_THAI_CHARS`` — keep both in step.
const MAX_SURNAME_THAI_CHARS: usize = 10;

/// FIX40 — ``NOT_SURNAME`` matched whole tokens only, so ``คุณสมชาย โทรมา``
/// kept ``โทรมา`` as a surname while ``คุณสมชาย โทร`` trimmed it. Thai glues
/// the verb to what follows, so this is a PREFIX check. Mirrors Python's
/// ``_is_not_surname``.
fn is_not_surname(tok: &str) -> bool {
    if NOT_SURNAME.contains(&tok) {
        return true;
    }
    // Only the LONGER entries (field labels and verbs) may match as a prefix:
    // the short particles in the set begin real surnames (``มากมี``). Length is
    // counted in CHARACTERS to match Python's ``len(w) >= 3``.
    NOT_SURNAME
        .iter()
        .any(|w| w.chars().count() >= 3 && tok.starts_with(w) && tok.len() > w.len())
}

fn thai_len(s: &str) -> usize {
    s.chars().filter(|c| ('\u{0e01}'..='\u{0e5b}').contains(c)).count()
}

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
        let chars = thai_gazetteer::Chars::new(text);

        for re in [&*THAI_NAME_RE, &*MIXED_NAME_RE] {
            for caps in re.captures_iter(text).engine_ok() {
                let whole = match caps.get(0) {
                    Some(g) => g,
                    None => continue,
                };
                let start_b = whole.start();
                let mut end_b = whole.end();

                // Trim an over-captured non-surname trailing word
                // (``คุณสมหญิง จำนวน 10,000`` → ``คุณสมหญิง``).
                let mut surname = caps.name("surname");
                if let Some(sn) = surname {
                    if is_not_surname(sn.as_str().trim()) {
                        surname = None;
                    }
                }
                // FIX29 + FIX40: title + one run that begins with a gazetteer
                // name glued to a dictionary word → the span ends at the name
                // (``คุณมีชัยจองห้องประชุมไว้`` → ``คุณมีชัย``). FIX40 runs this
                // ALWAYS, not only when the optional surname group is absent.
                let given = match caps.name("given") {
                    Some(g) => g,
                    None => continue,
                };
                let g_start_c = byte_to_char(text, given.start());
                let g_end_c = byte_to_char(text, given.end());
                let g_trim_c = thai_gazetteer::trim_run(&chars, g_start_c, g_end_c);
                if g_trim_c < g_end_c {
                    // the run was "name + prose": nothing after the space
                    // belongs to this person.
                    end_b = chars.byte_of(g_trim_c);
                } else {
                    match surname {
                        None => end_b = given.end(),
                        Some(sn) => {
                            // Length decides, on the RAW surname: ``trim_run``
                            // cuts real surnames too (``รักดี`` -> ``รัก``), so it
                            // is not used here. Mirrors Python.
                            let raw = sn.as_str();
                            let lead = raw.len() - raw.trim_start().len();
                            if thai_len(&text[sn.start() + lead..sn.end()]) > MAX_SURNAME_THAI_CHARS {
                                end_b = given.end(); // a clause, not a surname
                            } else {
                                end_b = sn.end();
                            }
                        }
                    }
                }
                let matched = &text[start_b..end_b];

                // Python ``_FALSE_POSITIVE_RE.match(matched)`` — anchored at
                // offset 0 of the (trimmed) span.
                let fp_hit = FALSE_POSITIVE_RE
                    .find(matched)
                    .engine_or(None)
                    .map(|x| x.start() == 0)
                    .unwrap_or(false);
                if fp_hit {
                    continue;
                }

                let s = byte_to_char(text, start_b);
                let e = byte_to_char(text, end_b);
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: matched.to_string(),
                        confidence: 1.0,
                        priority: 0,
                        entity_type: None,
                    });
                }
            }
        }
        out
    }
}

#[cfg(test)]
mod fix40_swallow_tests {
    //! FIX40 — the titled detector must stop at the name, not eat the clause.
    use super::ThaiNameDetector;
    use crate::detectors::base::Detector;

    fn hits(text: &str) -> Vec<String> {
        ThaiNameDetector
            .detect(text)
            .into_iter()
            .map(|m| m.matched_text)
            .collect()
    }

    #[test]
    fn a_name_glued_to_prose_stops_at_the_name() {
        assert_eq!(
            hits("คุณแก้วโทรมาบอกว่าแก้วน้ำที่สั่งไปแตกสองใบ ขอให้ส่งชุดใหม่ไปแทน"),
            vec!["คุณแก้ว"]
        );
    }

    #[test]
    fn a_clause_after_the_space_is_not_a_surname() {
        assert_eq!(hits("คุณวิชัย เสนอให้เลื่อนเปิดตัวไปเดือนหน้า"), vec!["คุณวิชัย"]);
        assert_eq!(hits("คุณพิมพ์ใจ ลาป่วยสามวัน ตั้งแต่วันจันทร์"), vec!["คุณพิมพ์ใจ"]);
    }

    #[test]
    fn a_real_surname_is_still_masked() {
        assert_eq!(hits("ลูกค้าชื่อคุณสมชาย ใจดี โทรมาบ่น"), vec!["คุณสมชาย ใจดี"]);
        assert_eq!(hits("คุณสมชาย โทรมา"), vec!["คุณสมชาย"]);
        assert_eq!(hits("คุณประเสริฐ ทองดี แจ้งว่ากระเบื้องบิ่น"), vec!["คุณประเสริฐ ทองดี"]);
        assert_eq!(hits("คุณสมหญิง รักดี โทรมา"), vec!["คุณสมหญิง รักดี"]);
    }
}
