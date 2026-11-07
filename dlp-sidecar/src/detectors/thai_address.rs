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
// 8 Oct 2026 — two openers more (mirrors thai_address.py; found by asking the engine about lead-ins one
// by one). ``ส่งที่`` was the only way of saying "send it to" this detector heard, and a house number was
// an address only after one of a handful of words: ``ส่งเอกสารไปที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ`` and
// ``99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500`` on a line of its own were not found.
//
// DELIVERY — sending and collecting, said with an object or a direction: ส่ง<what>ไปที่ / ส่ง<what>มาที่ /
// จัดส่ง<what>ที่ / ส่งถึง / นัดรับ<what>ที่ / มารับที่. The AMBIGUOUS grade exactly (a digit near AND an address
// part). <what> is at most 14 characters with no space, no digit and no ``ที่`` in it, so plain ``ส่งที่`` /
// ``จัดส่งที่`` stay with AMBIG_KW and mask as they always did. What is masked begins AFTER the lead-in.
const WHAT: &str = r"(?:(?!ที่)[^\s\d])";
// HOUSE NUMBER FIRST — no lead-in at all: a number that starts a token, a street-level part right after
// it, and a second address part or a postcode further on. (A Thai word glued in front is no reason
// against: Thai writes no space there. What the lookbehind keeps out is the tail of a longer number or
// code: A12, 1,250, 10:30, 02-123.)
const NUMBER_STARTS_TOKEN: &str = r"(?<![\d/\-.,:A-Za-z])";
// Group 1 = a keyword opener (+separators) when one matched; the group named ``body`` = the body.
const BODY: &str = r"(?P<body>[^\n]{3,200}?\d{5}(?=[\s,]|$)|[^\n]{3,200}?(?=\n|$|\s{3,}))";

// Sara am, one spelling (mirrors thai_address.py). The sidecar NFKC-normalises its input (main.rs), which
// splits ``ำ`` (U+0E33) into nikhahit + sara aa (U+0E4D U+0E32); the word lists are written the way people
// type. So ``ตำบล`` / ``อำเภอ`` / ``ลำปาง`` in the lists never matched the text a chat is scanned as. Both
// spellings are one word here: the regexes take either, and a token is put back into the typed spelling
// before it is compared or measured.
const AM: &str = "\u{0E33}";
const AM_SPLIT: &str = "\u{0E4D}\u{0E32}";

/// ``tok`` as a person types it: a split sara am put back together.
fn typed(tok: &str) -> String {
    tok.replace(AM_SPLIT, AM)
}

fn alt(words: &[&str]) -> String {
    let either = format!("(?:{}|{})", AM, AM_SPLIT);
    words
        .iter()
        .map(|w| fancy_regex::escape(w).into_owned().replace(AM, &either))
        .collect::<Vec<_>>()
        .join("|")
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
    let delivery = format!(
        "(?:(?:จัด)?ส่ง{what}{{1,14}}?ที่|(?:จัด)?ส่งถึง(?:ที่)?|(?:นัด|มา|ไป)?รับ{what}{{0,14}}?ที่)",
        what = WHAT
    );
    let house_no_first = format!(
        "{nst}(?=\\d+(?:/\\d+)?[\\s,]+(?:{street})\\S{{0,25}}[\\s,]+[^\\n]{{0,120}}?{nat}(?:{comp}|\\d{{5}}(?=[\\s,]|$)))",
        nst = NUMBER_STARTS_TOKEN, street = street_alt, nat = NOT_AFTER_THAI, comp = comp_alt
    );
    // The keyword openers are tried first at every position (group 1), so everything that matched
    // before matches as it did; the two after them have no group of their own — what is masked
    // begins where the body does.
    let pat = format!(
        "(?:({high}[\\s:：]*{dn}|{amb}[\\s:：]*{dn}{comp}|{lead}{hnc}[\\s:：]*|ที่{cnc}[\\s:：]*)|{delivery}[\\s:：]*{dn}{comp}|{hnf}){body}",
        high = HIGH_KW,
        amb = AMBIG_KW,
        dn = DIGIT_NEAR,
        comp = component,
        lead = LEADIN_KW,
        hnc = house_no_then_component,
        cnc = component_number_component,
        delivery = delivery,
        hnf = house_no_first,
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
// The address is the match — never "the rest of the line" (mirrors _LEAD_WINDOW in thai_address.py).
// Until 2026-10-08 a walk that kept nothing returned the end of the body, and it kept nothing whenever the
// first token after the keyword was a place's own name longer than 12 characters — ``สำนักงานใหญ่`` is 13
// once NFKC has split its sara am — so ``ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด`` was
// masked to its last word and no model was shown the request. A token before the first address part is
// the place's name and is kept whatever its length, while it begins inside the gate's own reach (the house
// number that qualified the match is within 30 characters of the keyword); and there is no fallback.
const LEAD_WINDOW: usize = 30;

fn is_postcode(tok: &str) -> bool {
    tok.len() == 5 && tok.bytes().all(|b| b.is_ascii_digit())
}

fn is_component_token(tok: &str, next_tok: Option<&str>) -> bool {
    let tok = typed(tok);
    let tok = tok.as_str();
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
    let tok = typed(tok);
    let tok_len = tok.chars().count();
    CLAUSE_STARTS.iter().any(|w| {
        tok.starts_with(w) && !(w.chars().count() <= 2 && tok_len > SHORT_PREFIX_MAX_TOKEN)
    })
}

fn bare_token_ends(tok: &str) -> bool {
    let tok = typed(tok);
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
        let lead = !seen_component && body[..s].chars().count() <= LEAD_WINDOW;
        if !lead {
            if bare_token_ends(tok) {
                break;
            }
            bare_run += 1;
            if bare_run > MAX_BARE_RUN {
                break;
            }
        }
        end = tok_end;
    }
    end
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
            let g2 = match caps.name("body") {
                Some(g) => g,
                None => continue,
            };
            // from the keyword when a keyword opened the match; from the place when a delivery
            // lead-in or a house number did
            let start = caps.get(1).map(|g| g.start()).unwrap_or_else(|| g2.start());
            let g2_end = trim_address_end(text, g2.start(), g2.end());
            let full = &text[start..g2_end];
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
                start: byte_to_char(text, start),
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

#[cfg(test)]
mod tail_tests {
    //! 2026-10-08 — the address is the match, never the rest of the line. The same sentences the Python
    //! tests pin (backend/tests/test_dlp_detectors/test_thai_address_tail.py), each in both spellings of
    //! sara am: as typed, and as the sidecar's NFKC pass hands it to this detector.
    use super::ThaiAddressDetector;
    use crate::detectors::base::Detector;

    fn spans(text: &str) -> Vec<String> {
        ThaiAddressDetector.detect(text).into_iter().map(|m| m.matched_text).collect()
    }

    fn as_scanned(text: &str) -> String {
        text.replace("\u{0E33}", "\u{0E4D}\u{0E32}")
    }

    fn both(text: &str, masked: &str) {
        assert_eq!(spans(text), vec![masked.to_string()], "as typed: {text}");
        assert_eq!(spans(&as_scanned(text)), vec![as_scanned(masked)], "as scanned: {text}");
    }

    #[test]
    fn what_a_person_types_after_an_address_is_not_part_of_it() {
        let address = "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ";
        both(&format!("ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน {address} ขอให้เรียงจากถูกสุด"), address);
        both(
            &format!("ขอซื้อเก้าอี้สำนักงาน 20 ตัว {address} งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด"),
            address,
        );
        both(address, address);
    }

    #[test]
    fn a_long_word_before_the_house_number_is_the_places_name() {
        both(
            "ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ ช่วยตรวจว่าส่งได้ไหม",
            "ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ",
        );
        both(
            "ส่งที่อาคารสาทรธานี ชั้น 12 ถนนสาทรเหนือ กรุงเทพฯ ภายในวันศุกร์ ขอใบเสนอราคาด้วย",
            "ส่งที่อาคารสาทรธานี ชั้น 12 ถนนสาทรเหนือ กรุงเทพฯ",
        );
    }

    #[test]
    fn tambon_and_amphoe_are_address_parts_in_the_spelling_a_scan_sees() {
        // before: with the sara am split, ตําบล / อําเภอ matched nothing in the lists
        both(
            "ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง ขอบคุณครับ",
            "ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง",
        );
    }

    #[test]
    fn a_walk_that_keeps_nothing_is_not_an_address_to_the_end_of_the_line() {
        let text = format!("ที่อยู่{}เรื่องที่เขาพูดถึงกันเมื่อวานนี้ในที่ประชุมใหญ่ของบริษัท", " ".repeat(40));
        assert!(spans(&text).is_empty());
    }
}

#[cfg(test)]
mod lead_in_tests {
    //! 2026-10-08 — an address is an address whatever was typed before it. The sentences the Python tests
    //! pin (backend/tests/test_dlp_detectors/test_thai_address_lead_in.py), in both spellings of sara am.
    use super::ThaiAddressDetector;
    use crate::detectors::base::Detector;

    const OFFICE: &str = "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ";
    const FULL: &str = "99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500";
    const TAIL: &str = " ภายในวันศุกร์นะครับ";
    const DELIVERY: &[&str] = &[
        "ส่งเอกสารไปที่", "จัดส่งไปที่", "ส่งของมาที่", "นัดรับที่", "ส่งไปที่", "ส่งมาที่", "จัดส่งสินค้ามาที่", "ส่งถึง",
        "ส่งให้ที่", "นัดรับของที่", "มารับที่", "รับของที่", "รับสินค้าที่",
    ];

    fn spans(text: &str) -> Vec<String> {
        ThaiAddressDetector.detect(text).into_iter().map(|m| m.matched_text).collect()
    }

    fn as_scanned(text: &str) -> String {
        text.replace("\u{0E33}", "\u{0E4D}\u{0E32}")
    }

    fn both(text: &str, masked: &str) {
        assert_eq!(spans(text), vec![masked.to_string()], "as typed: {text}");
        assert_eq!(spans(&as_scanned(text)), vec![as_scanned(masked)], "as scanned: {text}");
    }

    #[test]
    fn a_delivery_lead_in_opens_an_office_address_and_stays_in_front_of_it() {
        for lead in DELIVERY {
            both(&format!("ขอบคุณครับ {lead}{OFFICE}{TAIL}"), OFFICE);
        }
    }

    #[test]
    fn a_whole_address_is_found_whatever_comes_before_it() {
        for lead in DELIVERY.iter().chain(["เจอกันที่", "พักที่", "ทำงานที่", ""].iter()) {
            both(&format!("ขอบคุณครับ {lead}{FULL}{TAIL}"), FULL);
        }
        both(FULL, FULL);
        both("12 ตำบลบางรัก อำเภอเมือง ลำปาง ขอบคุณครับ", "12 ตำบลบางรัก อำเภอเมือง ลำปาง");
        both(
            "88 หมู่ 3 ตำบลบางพลีใหญ่ อำเภอบางพลี สมุทรปราการ 10540 ส่งได้เลย",
            "88 หมู่ 3 ตำบลบางพลีใหญ่ อำเภอบางพลี สมุทรปราการ 10540",
        );
    }

    #[test]
    fn what_matched_before_matches_as_it_did() {
        both(&format!("ส่งที่{OFFICE}{TAIL}"), &format!("ส่งที่{OFFICE}"));
        both(&format!("จัดส่งที่{OFFICE}{TAIL}"), &format!("ส่งที่{OFFICE}"));
        both(&format!("ที่อยู่ {FULL}{TAIL}"), &format!("ที่อยู่ {FULL}"));
        both("ไปที่ 23 ตึกบี ห้อง 402", "ไปที่ 23 ตึกบี ห้อง 402");
        both("เขาอยู่ 88/4 ถนนสุขุมวิท กรุงเทพ", "อยู่ 88/4 ถนนสุขุมวิท กรุงเทพ");
        // ส่งไปที่ is a delivery lead-in of its own now: masked from the house number on
        both("ส่งไปที่ 199/22 คอนโดลุมพินี ชั้น 8 ห้อง 812", "199/22 คอนโดลุมพินี ชั้น 8 ห้อง 812");
    }

    #[test]
    fn prose_is_not_an_address() {
        for text in [
            "ส่งเอกสารไปที่ฝ่ายบัญชีภายใน 5 วันทำการนะครับ",
            "ส่งงานไปที่หัวหน้าแล้ว 2 รอบ ยังไม่ตอบ",
            "นัดรับที่ร้านกาแฟ 3 โมงเย็น",
            "มารับที่หน้าบริษัทได้เลย รอ 10 นาที",
            "ได้รับที่ปรึกษาใหม่ 2 คนในไตรมาสนี้",
            "ส่งเสริมการขายที่สาขา 5 แห่งทั่วประเทศ",
            "ส่งถึงมือลูกค้าภายใน 3 วัน",
            "เดินไปอีก 3 ซอย ก็ถึงแล้ว",
            "ร้านนี้มี 2 ถนนให้เลือกเข้า",
            "ครั้งที่ 3 ถนนคนเดินเปิดถึงเที่ยงคืน",
            "ราคา 1200 บาท ต่อ 3 ชั้น",
            "ไปที่หน้า 3 แล้วกดปุ่มสีเขียว",
            "นัดที่ห้องประชุมชั้น 2 ตอนบ่าย",
            "ประชุมที่ห้อง 402 ชั้น 5",
            "ไปที่ 3 โมงเย็นพอดีนะ อย่าสาย",
            "เลขที่ให้จำเมื่อกี้คืออะไร",
            "โทร 02 123 4567 ต่อ 12",
            "ออเดอร์ 12 ชิ้น ส่งวันที่ 5 ตุลาคม",
        ] {
            assert!(spans(text).is_empty(), "as typed: {text} -> {:?}", spans(text));
            assert!(spans(&as_scanned(text)).is_empty(), "as scanned: {text}");
        }
    }

    #[test]
    fn a_number_that_does_not_start_a_token_is_not_a_house_number() {
        for (text, not) in [
            ("รหัส A12 ถนนสีลม แขวงสีลม", "12 "),
            ("ยอด 1,250 ถนนสีลม แขวงสีลม", "250 "),
            ("เวลา 10:30 ถนนสีลม แขวงสีลม", "30 "),
            ("โทร 02-99 ถนนสีลม แขวงสีลม", "99 "),
        ] {
            assert!(spans(text).iter().all(|s| !s.starts_with(not)), "{text} -> {:?}", spans(text));
        }
        both("พักที่99/9 ถนนสีลม แขวงสีลม ครับ", "99/9 ถนนสีลม แขวงสีลม");
    }
}
