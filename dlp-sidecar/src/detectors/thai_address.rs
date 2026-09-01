//! Thai address detector — port of ``thai_address.py``.
//!
//! Two-group regex (keyword + body). Span is start of group 1
//! through end of group 2 of the regex match (Python: m.start(1),
//! m.end(2)).
//!
//! Precision/recall stance (FIX26 §1.2 keyword-precision split): HIGH
//! keywords (บ้านเลขที่ / อยู่บ้านเลขที่ / phrase-initial ที่อยู่) fire on a
//! house number within 30 chars alone; AMBIGUOUS keywords (เลขที่ / อยู่ที่ /
//! ส่งที่) keep the strict FIX23 rule (digit AND component/postcode).
//! Remaining gaps: no house number, >30-char prose gap, ambiguous-keyword
//! addresses with only a building/estate/bare-province name.
//!
//! FIX23 — the body must (a) carry a digit within 30 chars of the
//! keyword (house number) AND (b) contain an address component on the
//! same line (ถนน/ซอย/หมู่/ตำบล/อำเภอ/จังหวัด/แขวง/เขต/กรุงเทพ/กทม or a
//! 5-digit postcode). Without those anchors, ordinary Thai prose
//! containing ``เลขที่`` ("the number that…") / ``อยู่ที่`` ("is at") /
//! ``ที่อยู่`` was swept to end-of-line as an "address" — the question
//! ``เลขที่ให้จำเมื่อกี้คืออะไร`` became ``<<ADDRESS_1>>`` on input (the
//! model echoed the token back), and the answer ``เลขที่ให้จำคือ 74-…``
//! was masked on output. Keep this regex byte-identical to Python.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

// FIX26 §1.2 — keyword-precision split, mirrored from thai_address.py:
//   HIGH (อยู่บ้านเลขที่ / บ้านเลขที่ / phrase-initial ที่อยู่ not followed
//   by a locative preposition): digit within 30 chars is enough.
//   AMBIGUOUS (เลขที่ / อยู่ที่ / ส่งที่): digit AND address component.
// Group 1 = keyword (+separators), group 2 = body — same as before.
const HIGH_KW: &str = r"(?:อยู่บ้านเลขที่|บ้านเลขที่|(?<![\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}])ที่อยู่(?![\s:：]*(?:ใน|ตรง|บน|ใต้|ข้าง|ระหว่าง|ใกล้|แถว|กับ|ไหน)))";
// A glued ที่อยู่ (จำที่อยู่ …) drops to the AMBIGUOUS grade, not to nothing.
const AMBIG_KW: &str = r"(?:เลขที่|ที่อยู่|อยู่ที่|ส่งที่)";
const DIGIT_NEAR: &str = r"(?=[^\n\d]{0,30}\d)";
const COMPONENT: &str = r"(?=[^\n]{0,200}?(?:ถนน|ถ\.|ซอย|ซ\.|หมู่|ม\.|ตำบล|ต\.|อำเภอ|อ\.|จังหวัด|จ\.|แขวง|เขต|กรุงเทพ|กทม|\d{5}(?=[\s,]|$)))";
const BODY: &str = r"([^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))";

static ADDRESS_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        "({high}[\\s:：]*{dn}|{amb}[\\s:：]*{dn}{comp}){body}",
        high = HIGH_KW,
        amb = AMBIG_KW,
        dn = DIGIT_NEAR,
        comp = COMPONENT,
        body = BODY,
    );
    Regex::new(&pat).expect("thai address regex")
});

// FIX38 Part G — where the address ENDS. Mirrors ``trim_address_end`` in
// thai_address.py: the body was anchored on end-of-line, so with no
// postcode ``ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ …`` masked the
// whole rest of the turn. Walk whitespace tokens: keep address-ish ones
// (digit-bearing / component-word-initial / กรุงเทพ), allow up to two bare
// Thai tokens in a row (district + province), stop at a clause-starting
// verb or particle or at the third bare token.
const COMPONENT_STARTS: &[&str] = &[
    "ถนน", "ถ.", "ซอย", "ซ.", "หมู่บ้าน", "หมู่", "ม.", "ตำบล", "ต.", "อำเภอ",
    "อ.", "จังหวัด", "จ.", "แขวง", "เขต", "กรุงเทพ", "กทม", "อาคาร", "ตึก",
    "ชั้น", "ห้อง", "คอนโด", "อพาร์ทเม้นท์", "หอพัก", "นิคม", "บ้าน", "เลขที่",
    "แยก", "ตรอก", "ริม", "ใกล้", "ตรงข้าม", "หลัง", "ข้าง",
];
const CLAUSE_STARTS: &[&str] = &[
    "สั่ง", "ส่ง", "ช่วย", "ขอ", "แล้ว", "และ", "แต่", "ให้", "เพราะ", "ถ้า",
    "ครับ", "ค่ะ", "คะ", "นะ", "จะ", "ได้", "มี", "เป็น", "ไป", "มา", "บอก",
    "แจ้ง", "โทร", "เบอร์", "ติดต่อ", "อยาก", "ต้อง", "ไม่", "ซื้อ", "ขาย",
    "จอง", "เรียน", "รบกวน", "ฝาก", "ตอนนี้", "วันนี้", "พรุ่งนี้", "เมื่อวาน",
    "ของ", "สินค้า", "พัสดุ", "เอกสาร", "ลูกค้า", "คุณ", "พี่", "น้อง",
    "ยัง", "เพิ่ง", "กำลัง", "เคย", "ค่อย", "รอ", "เจอ", "พบ", "ถาม", "ตอบ",
    "ทำ", "ดู", "เช็ค", "ตรวจ", "แก้", "เปลี่ยน", "ยกเลิก", "คืน", "จ่าย",
    "โอน", "รับ", "เอา", "ใช้", "ลอง", "เปิด", "ปิด", "หา", "นัด", "ประชุม",
];
const MAX_BARE_RUN: usize = 2;

fn is_component_token(tok: &str) -> bool {
    tok.chars().any(|c| c.is_ascii_digit() || ('๐'..='๙').contains(&c))
        || COMPONENT_STARTS.iter().any(|p| tok.starts_with(p))
}

fn starts_clause(tok: &str) -> bool {
    CLAUSE_STARTS.iter().any(|p| tok.starts_with(p))
}

/// Byte offset where the address ends inside ``text[body_start..body_end]``.
fn trim_address_end(text: &str, body_start: usize, body_end: usize) -> usize {
    let body = &text[body_start..body_end];
    let mut end = body_start;
    let mut bare_run = 0usize;
    let mut seen_component = false;
    let mut pos = 0usize;
    for tok in body.split_whitespace() {
        // locate this token (split_whitespace gives no offsets)
        let rel = body[pos..].find(tok).map(|i| pos + i).unwrap_or(pos);
        let tok_end = body_start + rel + tok.len();
        pos = rel + tok.len();
        if is_component_token(tok) {
            end = tok_end;
            bare_run = 0;
            seen_component = true;
            continue;
        }
        if seen_component && starts_clause(tok) {
            break;
        }
        bare_run += 1;
        if bare_run > MAX_BARE_RUN {
            break;
        }
        end = tok_end;
    }
    if end > body_start {
        end
    } else {
        body_end
    }
}

pub struct ThaiAddressDetector;

impl Detector for ThaiAddressDetector {
    fn name(&self) -> &'static str {
        "thai_address"
    }
    fn entity_type(&self) -> &'static str {
        "thai_address"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in ADDRESS_RE.captures_iter(text).flatten() {
            let g1 = match caps.get(1) {
                Some(g) => g,
                None => continue,
            };
            let g2 = match caps.get(2) {
                Some(g) => g,
                None => continue,
            };
            let g2_end = trim_address_end(text, g2.start(), g2.end());
            let full = &text[g1.start()..g2_end];
            let matched = full.trim();
            if matched.chars().count() < 6 {
                continue;
            }
            // The trim may have dropped the digit that qualified the match.
            if !text[g2.start()..g2_end]
                .chars()
                .any(|c| c.is_ascii_digit() || ('๐'..='๙').contains(&c))
            {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, g1.start()),
                end: byte_to_char(text, g2_end),
                matched_text: matched.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}

#[cfg(test)]
mod end_trim_tests {
    //! FIX38 Part G — the address stops where the clause does.
    use super::ThaiAddressDetector;
    use crate::detectors::base::Detector;

    fn spans(text: &str) -> Vec<String> {
        ThaiAddressDetector
            .detect(text)
            .into_iter()
            .map(|m| m.matched_text)
            .collect()
    }

    #[test]
    fn the_owners_sentence_keeps_the_order_and_the_request() {
        let t = "พี่ตูน โทร 081-234-5678 ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความตอบลูกค้าให้หน่อย";
        assert_eq!(spans(t), vec!["ที่อยู่ 55/12 ซอย 5 บางนา"]);
    }

    #[test]
    fn district_and_province_without_keywords_are_kept() {
        assert_eq!(spans("ที่อยู่ 77/1 บางใหญ่ นนทบุรี ตอนนี้ไม่มีคนอยู่"), vec!["ที่อยู่ 77/1 บางใหญ่ นนทบุรี"]);
    }

    #[test]
    fn a_postcode_still_ends_the_body() {
        assert_eq!(spans("ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540 ส่งด่วนนะ"),
                   vec!["ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540"]);
    }
}
