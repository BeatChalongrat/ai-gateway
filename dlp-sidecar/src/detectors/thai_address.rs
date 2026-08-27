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
            let full = &text[g1.start()..g2.end()];
            let matched = full.trim();
            if matched.chars().count() < 6 {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, g1.start()),
                end: byte_to_char(text, g2.end()),
                matched_text: matched.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
