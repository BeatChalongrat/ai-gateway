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
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

// FIX46 — the word lists are the SAME FILES the Python detector reads
// (backend/app/services/dlp/data/*.txt ≡ dlp-sidecar/data/*.txt; a Python
// test hashes both copies). Before this the gate regex and the trim tuple
// were typed twice and had drifted — the gate did not know ตึก/อาคาร/ห้อง/
// ชั้น/คอนโด, so ``อยู่ที่ 23 ตึกบี ห้อง 402`` never entered.
const COMPONENTS_TXT: &str = include_str!("../../data/thai_address_components.txt");
const TRIM_ONLY_TXT: &str = include_str!("../../data/thai_address_trim_words.txt");
const CLAUSE_TXT: &str = include_str!("../../data/thai_address_clause_starts.txt");
const LOCALITIES_TXT: &str = include_str!("../../data/thai_address_localities.txt");

fn words(txt: &'static str) -> Vec<&'static str> {
    txt.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).collect()
}

static GATE_COMPONENTS: Lazy<Vec<&'static str>> = Lazy::new(|| words(COMPONENTS_TXT));
static COMPONENT_STARTS: Lazy<Vec<&'static str>> = Lazy::new(|| {
    let mut v = words(COMPONENTS_TXT);
    v.extend(words(TRIM_ONLY_TXT));
    v
});
static CLAUSE_STARTS: Lazy<Vec<&'static str>> = Lazy::new(|| words(CLAUSE_TXT));
static CLAUSE_INSIDE: Lazy<Vec<&'static str>> =
    Lazy::new(|| words(CLAUSE_TXT).into_iter().filter(|w| w.chars().count() >= 3).collect());
static LOCALITIES: Lazy<HashSet<&'static str>> = Lazy::new(|| words(LOCALITIES_TXT).into_iter().collect());

// FIX26 §1.2 — keyword-precision split, mirrored from thai_address.py:
//   HIGH (อยู่บ้านเลขที่ / บ้านเลขที่ / phrase-initial ที่อยู่ not followed
//   by a locative preposition): digit within 30 chars is enough.
//   AMBIGUOUS (เลขที่ / อยู่ที่ / ส่งที่): digit AND address component.
// Group 1 = keyword (+separators), group 2 = body — same as before.
const HIGH_KW: &str = r"(?:อยู่บ้านเลขที่|บ้านเลขที่|(?<![\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}])ที่อยู่(?![\s:：]*(?:ใน|ตรง|บน|ใต้|ข้าง|ระหว่าง|ใกล้|แถว|กับ|ไหน)))";
// A glued ที่อยู่ (จำที่อยู่ …) drops to the AMBIGUOUS grade, not to nothing.
const AMBIG_KW: &str = r"(?:เลขที่|ที่อยู่|อยู่ที่|ส่งที่)";
const DIGIT_NEAR: &str = r"(?=[^\n\d]{0,30}\d)";
// FIX46 #6 — lead-in words (ไปที่ / มาที่ / นัดที่ / แวะที่ / bare อยู่): a
// house number IMMEDIATELY after them, then an address part. Mirrors
// _LEADIN_KW + _HOUSE_NO_THEN_COMPONENT in Python.
const LEADIN_KW: &str = r"(?:ไปที่|มาที่|นัดที่|แวะที่|อยู่)";
// A gate component must START a token (not glued after a Thai letter):
// ``ต่อชั้น`` is prose, ``ชั้น 5`` is an address part.
const NOT_AFTER_THAI: &str = r"(?<![\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}])";
// FIX46 #5 (sf03) — bare ``ที่`` + <street-level part><name> <number> + a
// second part. First part street-level only (rooms/floors are meeting places).
const STREET_WORDS: &[&str] = &["ซอย", "ซ.", "ถนน", "ถ.", "หมู่บ้าน", "หมู่", "ตำบล", "ต.", "แขวง", "เขต"];
const BODY: &str = r"([^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))";

fn alt(words: &[&str]) -> String {
    words.iter().map(|w| fancy_regex::escape(w).into_owned()).collect::<Vec<_>>().join("|")
}

static ADDRESS_RE: Lazy<Regex> = Lazy::new(|| {
    let comp_alt = alt(&GATE_COMPONENTS);
    let street_alt = alt(STREET_WORDS);
    let component = format!("(?=[^\\n]{{0,200}}?{nat}(?:{comp}|\\d{{5}}(?=[\\s,]|$)))", nat = NOT_AFTER_THAI, comp = comp_alt);
    let house_no_then_component = format!(
        "(?=[\\s:：]*\\d+(?:/\\d+)?(?:[\\s,]+|$)\\s*{nat}(?:{comp}))",
        nat = NOT_AFTER_THAI, comp = comp_alt
    );
    let component_number_component = format!(
        "(?=\\s*(?:{street})\\S{{0,25}}\\s+\\d[\\d/\\-]*(?:[\\s,]+|$)[^\\n]{{0,120}}?(?:{comp}|\\d{{5}}(?=[\\s,]|$)))",
        street = street_alt, comp = comp_alt
    );
    let pat = format!(
        "({high}[\\s:：]*{dn}|{amb}[\\s:：]*{dn}{comp}|{lead}{hnc}[\\s:：]*|ที่{cnc}[\\s:：]*){body}",
        high = HIGH_KW,
        amb = AMBIG_KW,
        dn = DIGIT_NEAR,
        comp = component,
        lead = LEADIN_KW,
        hnc = house_no_then_component,
        cnc = component_number_component,
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
const MAX_BARE_RUN: usize = 2;
// FIX46 #5 (hr11) — see the Python comment: a two-character clause prefix
// (ขอ/มา/มี/หา …) only counts on a short token (≤ 5 chars), never on a
// 7-character province (ขอนแก่น/หาดใหญ่/มีนบุรี/มาบตาพุด); localities are
// address parts outright; a bare token before a 5-digit postcode is the
// "province + postcode" unit; a bare token longer than 12 chars or carrying
// a clause word inside it ends the address (glued clauses are one token).
const SHORT_PREFIX_MAX_TOKEN: usize = 5;
const BARE_MAX_CHARS: usize = 12;

fn is_postcode(tok: &str) -> bool {
    tok.len() == 5 && tok.bytes().all(|b| b.is_ascii_digit())
}

fn is_component_token(tok: &str, next_tok: Option<&str>) -> bool {
    if tok.chars().any(|c| c.is_ascii_digit() || ('๐'..='๙').contains(&c)) {
        return true;
    }
    if COMPONENT_STARTS.iter().any(|p| tok.starts_with(p)) {
        return true;
    }
    if LOCALITIES.contains(tok.trim_matches(|c| c == ',' || c == '.' || c == ';' || c == ':')) {
        return true;
    }
    matches!(next_tok, Some(n) if is_postcode(n))
}

fn starts_clause(tok: &str) -> bool {
    let tok_len = tok.chars().count();
    CLAUSE_STARTS.iter().any(|w| {
        tok.starts_with(w) && !(w.chars().count() <= 2 && tok_len > SHORT_PREFIX_MAX_TOKEN)
    })
}

fn bare_token_ends(tok: &str) -> bool {
    tok.chars().count() > BARE_MAX_CHARS || CLAUSE_INSIDE.iter().any(|w| tok.contains(w))
}

/// Byte offset where the address ends inside ``text[body_start..body_end]``.
fn trim_address_end(text: &str, body_start: usize, body_end: usize) -> usize {
    let body = &text[body_start..body_end];
    // (byte start, byte end) of every whitespace-delimited token
    let mut toks: Vec<(usize, usize)> = Vec::new();
    let mut pos = 0usize;
    for tok in body.split_whitespace() {
        let rel = body[pos..].find(tok).map(|i| pos + i).unwrap_or(pos);
        toks.push((rel, rel + tok.len()));
        pos = rel + tok.len();
    }
    let mut end = body_start;
    let mut bare_run = 0usize;
    let mut seen_component = false;
    for (i, &(s, e)) in toks.iter().enumerate() {
        let tok = &body[s..e];
        let next_tok = toks.get(i + 1).map(|&(ns, ne)| &body[ns..ne]);
        let tok_end = body_start + e;
        if is_component_token(tok, next_tok) {
            end = tok_end;
            bare_run = 0;
            seen_component = true;
            continue;
        }
        if seen_component && starts_clause(tok) {
            break;
        }
        if bare_token_ends(tok) {
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
        for caps in ADDRESS_RE.captures_iter(text).engine_ok() {
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

    // FIX46 — #6 and #5, the same sentences the Python tests pin.
    #[test]
    fn lead_in_with_house_number_then_building_is_an_address() {
        assert_eq!(spans("ไปที่ 23 ตึกบี ห้อง 402"), vec!["ไปที่ 23 ตึกบี ห้อง 402"]);
        assert_eq!(spans("อยู่ที่ 23 ตึกบี ห้อง 402"), vec!["อยู่ที่ 23 ตึกบี ห้อง 402"]);
        assert_eq!(spans("เขาอยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130"), vec!["อยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130"]);
    }

    #[test]
    fn lead_in_without_the_shape_is_prose() {
        assert!(spans("ไปที่หน้า 3 แล้วกดปุ่มสีเขียว").is_empty());
        assert!(spans("นัดที่ห้องประชุมชั้น 2 ตอนบ่าย 2").is_empty());
        assert!(spans("ไปที่ 3 โมงเย็นพอดีนะ อย่าสาย").is_empty());
        assert!(spans("ราคาอยู่ที่ 1200 ต่อชั้น 2 ชิ้นได้ส่วนลด").is_empty());
        assert!(spans("ประชุมที่ห้อง 402 ชั้น 5 ของอาคารสำนักงานใหญ่ตอนบ่ายสองนะ").is_empty());
    }

    #[test]
    fn bare_thi_then_soi_number_khwaeng() {
        assert_eq!(spans("ไปวัดหน้างานที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ อาทิตย์หน้า เขาบอกว่าขอพรให้ปิดดีลได้"),
                   vec!["ที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ"]);
    }

    #[test]
    fn provinces_that_start_with_a_clause_word_are_kept() {
        assert_eq!(spans("ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000 ช่วยร่างข้อความทวงเอกสาร"),
                   vec!["ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000"]);
        assert_eq!(spans("ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110 ขอให้ส่งด่วน"),
                   vec!["ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110"]);
    }

    #[test]
    fn a_postcode_still_ends_the_body() {
        assert_eq!(spans("ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540 ส่งด่วนนะ"),
                   vec!["ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540"]);
    }
}
