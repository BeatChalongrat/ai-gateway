//! Fallback Thai-name detector (no title prefix) — port of
//! ``thai_name_no_prefix.py``: Pass 1 (two-word + context keyword), and since
//! FIX27 Pass 2 (gazetteer + sensitive context) and Pass 3 (person list).
//!
//! Two-word Thai-script run + context keyword within ±25 chars
//! **outside** the match span. The cursor walk is load-bearing —
//! a rejected first match must NOT swallow downstream tokens, so on
//! a reject the cursor advances by exactly **one codepoint**, not the
//! full match length (Python ``pos = start + 1`` on str).
//!
//! FIX26 §1.2 — stop rules. Thai has no spaces between words, so a
//! whitespace-delimited "word" in prose is usually a phrase; the FIX24
//! sweep saw ``ชนิด ตอบสั้นๆ`` and ``เบอร์ เลขบัตร`` masked as people. A
//! pair is rejected when either token is plainly not a name (EXACT
//! common vocabulary / starts with a function word or common verb /
//! ends with a particle or question word / contains ``ๆ``). When the
//! second token is a field LABEL (``เบอร์`` / ``ที่อยู่`` …) the span is
//! trimmed to the first token, kept only when a name label (``ชื่อ`` /
//! ``name``) immediately precedes it. The four lists below are the SAME
//! as Python's ``_STOP_EXACT`` / ``_STOP_PREFIX`` / ``_STOP_SUFFIX`` /
//! ``_TRAIL_LABELS`` — a Python test compares them to this file.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

// Same Thai-char class as thai_name.rs / thai_address. Two ranges.
const THAI_CHAR: &str = r"[\u{0E01}-\u{0E3A}\u{0E40}-\u{0E4E}]";

// Context keywords — keep order identical to Python so any future
// regex alternation order changes stay obvious in the diff.
const CONTEXT_KEYWORDS: &[&str] = &[
    "ชื่อ",
    "ผู้ใช้",
    "ลูกค้า",
    "พนักงาน",
    "ผู้ส่ง",
    "เจ้าของ",
    "ลงชื่อ",
    "ผู้รับ",
];

// English field labels (Python Fix #6) — part of the CONTEXT check only;
// they can never sit at a Thai-char position, so the NAME_RE lookahead
// keeps the Thai subset. The old port omitted these, so ``name: <Thai
// name>`` (a JSON/API payload) masked in Python and not in the live
// engine — FIX26 closed it.
const CONTEXT_KEYWORDS_ENGLISH: &[&str] = &[
    "name", "Name", "NAME", "customer", "employee", "staff", "member", "owner",
    "patient", "client", "user", "recipient", "defendant", "plaintiff", "suspect",
];

static CONTEXT_RE: Lazy<Regex> = Lazy::new(|| {
    let mut all: Vec<&str> = CONTEXT_KEYWORDS.to_vec();
    all.extend_from_slice(CONTEXT_KEYWORDS_ENGLISH);
    let pat = all.join("|");
    Regex::new(&pat).expect("name context regex")
});

static NAME_RE: Lazy<Regex> = Lazy::new(|| {
    let kw_alt = CONTEXT_KEYWORDS.join("|");
    // Mirror the Python construction:
    //   (?<!THAI)(?!(?:KW)(?:\s|$|[^THAI]))(THAI{2,15}\s+THAI{2,15})(?!THAI)
    let pat = format!(
        r"(?<!{tc})(?!(?:{kw})(?:\s|$|[^\u{{0E01}}-\u{{0E3A}}\u{{0E40}}-\u{{0E4E}}]))({tc}{{2,15}}\s+{tc}{{2,15}})(?!{tc})",
        tc = THAI_CHAR,
        kw = kw_alt,
    );
    Regex::new(&pat).expect("name regex")
});

const CONTEXT_WINDOW_CHARS: usize = 25;

// ── FIX26 stop lists (identical to thai_name_no_prefix.py) ──────────────
pub const STOP_EXACT: &[&str] = &[
    "ผม", "ฉัน", "เรา", "คุณ", "เขา", "เธอ", "มัน", "ใคร", "อะไร", "ไหน", "นี้", "นั้น",
    "โน้น", "นี่", "นั่น", "ไหม", "มั้ย", "ยังไง", "อย่างไร", "ทำไม", "เมื่อไหร่", "เท่าไหร่",
    "ชนิด", "ประเภท", "แบบ", "เบอร์", "เลขบัตร", "บัตร", "อีเมล", "ที่อยู่", "บัญชี",
    "รายงาน", "ไฟล์", "ข้อมูล", "ระบบ", "สินค้า", "ราคา", "ยอด", "จำนวน", "จํานวน",
    "เวลา", "วันนี้", "พรุ่งนี้", "เมื่อวาน", "ตอนนี้", "เดือน", "ปี", "ครั้ง", "อัน", "ชิ้น",
    "คน", "ทุกคน", "บางคน", "หลายคน", "เรื่อง", "งาน", "ประชุม", "บริษัท", "ทีม", "แผนก",
    "ตำแหน่ง", "ตําแหน่ง", "อายุ", "วันเกิด", "เงินเดือน", "โบนัส", "โทร", "หน้า", "ข้อ",
    "มา", "มี", "ดี", "ไป", "ใหม่", "เก่า", "มาก", "น้อย", "ใหญ่", "เล็ก",
    "แล้ว", "ยัง", "เลย", "ก็", "จึง", "เพราะ", "แต่", "หรือ", "และ", "กับ", "ของ",
    "ก่อน", "หลัง", "ตอน", "ช่วง", "ระหว่าง", "ด้วย", "เอง", "ทั้งหมด", "บ้าง", "อีก",
    "สั้นๆ", "ยาวๆ", "เร็วๆ", "ช้าๆ",
];
pub const STOP_PREFIX: &[&str] = &[
    "ช่วย", "ต้อง", "ควร", "ถาม", "ตอบ", "บอก", "ว่า", "ไม่", "ให้", "ได้", "ทำ", "ทํา",
    "ขอ", "อยาก", "จะ", "กำลัง", "กําลัง", "เคย", "ลอง", "ใช้", "เอา", "เขียน", "สร้าง",
    "แก้", "ส่ง", "รับ", "ดู", "ดึง", "หา", "เช็ก", "เช็ค", "ตรวจ", "สรุป", "แปล", "อธิบาย",
    "แนะนำ", "แนะนํา", "จัด", "เปิด", "ปิด", "เพิ่ม", "ลบ", "ลด", "คิด", "รู้", "เข้าใจ",
    "เห็น", "พูด", "คุย", "เล่า", "ฟัง", "อ่าน", "ซื้อ", "ขาย", "จ่าย", "โอน", "จอง",
    "เริ่ม", "หยุด", "เสร็จ", "ผ่าน", "พัง", "ขึ้น", "ลง", "ออก", "เข้า", "กลับ", "เปลี่ยน",
    "ที่", "ซึ่ง", "เมื่อ", "ถ้า", "หาก", "เพราะ", "แต่", "และ", "หรือ", "กับ", "ใน",
    "จาก", "ถึง", "ตาม", "โดย", "เพื่อ", "สำหรับ", "สําหรับ", "เกี่ยว", "ทุก", "บาง",
    "หลาย", "แต่ละ", "ก่อน", "หลัง", "ยัง", "แล้ว", "ก็", "ตอน", "ช่วง", "เรื่อง",
    "อย่าง", "แบบ", "เท่า", "เกิน", "ประมาณ", "ราว",
];
pub const STOP_SUFFIX: &[&str] = &[
    "นะ", "ครับ", "ค่ะ", "คะ", "จ้า", "จ๊ะ", "จ้ะ", "สิ", "เถอะ", "ล่ะ", "หรอ", "เหรอ",
    "ไหม", "มั้ย", "ยังไง", "อย่างไร", "อะไร", "ไหน", "เท่าไหร่", "เมื่อไหร่", "ทำไม", "ทําไม",
    "ด้วย", "เลย", "บ้าง", "หน่อย", "ที", "ๆ",
];
pub const TRAIL_LABELS: &[&str] = &[
    "เบอร์", "โทร", "ที่อยู่", "อีเมล", "เลขบัตร", "บัตร", "บัญชี", "เงินเดือน", "โบนัส",
    "ตำแหน่ง", "ตําแหน่ง", "แผนก", "อายุ", "วันเกิด", "จำนวน", "จํานวน",
];

static NAME_LABEL_BEFORE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)(?:ชื่อ|name)\s*[:：]?\s*$").expect("name label regex"));

fn is_stop_token(tok: &str) -> bool {
    if tok.contains('ๆ') || STOP_EXACT.contains(&tok) {
        return true;
    }
    if STOP_PREFIX.iter().any(|p| tok.starts_with(p)) {
        return true;
    }
    STOP_SUFFIX.iter().any(|s| tok.ends_with(s))
}


// ── FIX27 Part 3: Pass 2 + Pass 3 (were never ported; the live engine missed
// every gazetteer/list name Python caught). The three lists below are
// GENERATED from the Python sources (thai_common_names.json,
// _SENSITIVE_CONTEXT, _LIST_CONTEXT_KEYWORDS) — a Python test compares them.
/// Common Thai given names (≥3 codepoints), longest first — Python `_GAZETTEER`.
pub const GAZETTEER: &[&str] = &[
    "เกียรติศักดิ์", "เกรียงศักดิ์", "กิตติศักดิ์", "เสาวลักษณ์", "ศิริลักษณ์", "ประสิทธิ์",
    "กิตติพงศ์", "สมเกียรติ", "ทวีศักดิ์", "สุรศักดิ์", "พิมพ์ลภัส", "อภิสิทธิ์",
    "พงศ์พัฒน์", "กิตติชัย", "จักรพงษ์", "เสาวนีย์", "ภาณุพงศ์", "ประพันธ์",
    "ณัฐกานต์", "ประยุทธ์", "สมศักดิ์", "ประเสริฐ", "ดาวเรือง", "ชัยวัฒน์",
    "รุ่งทิวา", "ปรียานุช", "เสกสรรค์", "จิราภรณ์", "ศิริวรรณ", "วิโรจน์",
    "วราภรณ์", "สุพัตรา", "สุพรรณี", "ธีรพงศ์", "อนุสรณ์", "ปราณิศา",
    "บุญเลิศ", "วิเชียร", "ทัศนีย์", "อภิชาติ", "จุฑามาศ", "วีรยุทธ",
    "ณัฐพงษ์", "ธนวัฒน์", "พิมพ์ใจ", "รุ่งนภา", "วิรัตน์", "บุญช่วย",
    "มานิตย์", "สมบูรณ์", "ไพโรจน์", "จำเนียร", "ไพบูลย์", "วีรพงษ์",
    "ผ่องศรี", "ณัฐวุฒิ", "สมหญิง", "ชูชาติ", "ขวัญใจ", "จินตนา",
    "ศิริพร", "สุรชัย", "อัญชลี", "ฐิตาภา", "มิ้นท์", "ชาญชัย",
    "บุญยัง", "ศุภกิจ", "เมทินี", "ธนาธิป", "ศุภโชค", "จิราพร",
    "อัจฉรา", "ทัศนัย", "เฟิร์น", "สุภาพร", "สุชาติ", "กาญจนา",
    "ฐิติมา", "เมธาวี", "ประยูร", "สมบัติ", "วีรชัย", "ศุภชัย",
    "อุทัย", "สุภาพ", "กัลยา", "ลำดวน", "ทองคำ", "นิตยา",
    "ชัยพร", "นภาพร", "ไพศาล", "อดิศร", "อนุชา", "รัตนา",
    "มนตรี", "จักรี", "เบลล์", "ณัฐพล", "ปรียา", "กิตติ",
    "ดวงพร", "สมชาย", "อรุณี", "รัชนี", "บุญมี", "บรรพต",
    "ปัทมา", "พรชัย", "บรรจง", "บุญมา", "สมนึก", "ลัดดา",
    "จิตรา", "พิชญา", "วิชัย", "ดวงใจ", "พงศกร", "ยุวดี",
    "วรรณา", "ยุพิน", "ปราณี", "ไพรัช", "วิชิต", "วรรณี",
    "วราพร", "กานดา", "ทองใบ", "พรเทพ", "สุรพล", "ภูวดล",
    "ถาวร", "ภาณุ", "ธนพล", "นิภา", "จรัส", "สมใจ",
    "อุดม", "ยุพา", "วีระ", "ดำรง", "เกษม", "อุบล",
    "ศิริ", "สมพร", "มานพ", "โอ๊ค", "จรูญ", "สมาน",
    "ลำไย", "ปิยะ", "ถนอม", "สุภา", "ธนกร", "นฤมล",
    "มงคล", "เมธี", "อรุณ", "อุไร", "ทวี", "นภา",
];
/// Python `_SENSITIVE_CONTEXT` — a gazetteer hit is masked only near one of these.
pub const SENSITIVE_CONTEXT: &[&str] = &[
    "เงินเดือน", "เงิน", "โบนัส", "ค่าจ้าง", "รายได้", "รายรับ", "salary", "income",
    "โอน", "บัญชี", "เครดิต", "หนี้", "กู้", "ผ่อน", "ยอด", "เบอร์",
    "โทร", "อีเมล", "email", "ที่อยู่", "address", "บัตร", "เลขบัตร", "บัตรประชาชน",
    "พาสปอร์ต", "passport", "ป่วย", "โรค", "ผลตรวจ", "วินิจฉัย", "อาการ", "HbA1c",
    "HIV", "เบาหวาน", "ความดัน", "มะเร็ง", "แพ้", "รักษา", "จำเลย", "โจทก์",
    "ผู้ต้องหา", "ผู้เสียหาย", "ผู้ร้อง", "ข้อหา", "คดี", "ศาล", "พนักงาน", "ตำแหน่ง",
    "แผนก", "ฝ่าย", "ลาออก", "สมัคร",
];
/// Python `_LIST_CONTEXT_KEYWORDS` — presence enables Pass 3 list-item scanning.
pub const LIST_CONTEXT_KEYWORDS: &[&str] = &[
    "รายชื่อ", "พนักงาน", "ลูกค้า", "สมาชิก", "ผู้เข้าร่วม", "ผู้ร่วม", "ผู้รับ", "ผู้ส่ง",
    "เรียน", "CC", "cc", "employee", "customer", "staff", "member",
];
const SENSITIVE_WINDOW_CHARS: usize = 100;
const LIST_PREFIX: &str = r"(?:คุณ|นางสาว|น\.ส\.|นาย|นาง|ดร\.)";

static GAZETTEER_RE: Lazy<Regex> = Lazy::new(|| {
    let alt = GAZETTEER.iter().map(|n| regex_escape(n)).collect::<Vec<_>>().join("|");
    Regex::new(&format!(r"(?<!{tc})(?:{alt})(?!{tc})", tc = THAI_CHAR, alt = alt)).expect("gazetteer regex")
});
static SENSITIVE_RE: Lazy<Regex> = Lazy::new(|| {
    let alt = SENSITIVE_CONTEXT.iter().map(|n| regex_escape(n)).collect::<Vec<_>>().join("|");
    Regex::new(&format!("(?i)(?:{alt})", alt = alt)).expect("sensitive regex")
});
static LIST_CONTEXT_RE: Lazy<Regex> = Lazy::new(|| {
    let alt = LIST_CONTEXT_KEYWORDS.iter().map(|n| regex_escape(n)).collect::<Vec<_>>().join("|");
    Regex::new(&format!("(?:{alt})", alt = alt)).expect("list context regex")
});
// Python `_LIST_ITEM_RE` (MULTILINE): an item delimited by a list separator /
// colon / line boundary on both sides, with an optional title prefix.
static LIST_ITEM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?m)(?:(?<=[,，|;:
])|^)\s*(?P<pre>{pre}\s*)?(?P<name>{tc}{{2,15}})\s*(?=[,，|;
]|$)",
        pre = LIST_PREFIX,
        tc = THAI_CHAR
    ))
    .expect("list item regex")
});

fn regex_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 4);
    for c in s.chars() {
        if "\\.^$|?*+()[]{}".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

fn push_match(out: &mut Vec<RawMatch>, seen: &mut HashSet<(usize, usize)>, text: &str, start_b: usize, end_b: usize) {
    let s = byte_to_char(text, start_b);
    let e = byte_to_char(text, end_b);
    if seen.insert((s, e)) {
        out.push(RawMatch {
            start: s,
            end: e,
            matched_text: text[start_b..end_b].to_string(),
            confidence: 1.0,
            priority: -5,
            entity_type: None,
        });
    }
}

/// Pass 2 — gazetteer name with a sensitive keyword within ±100 chars.
fn detect_gazetteer(text: &str, out: &mut Vec<RawMatch>, seen: &mut HashSet<(usize, usize)>) {
    let total_chars = text.chars().count();
    let mut pos_b = 0usize;
    while pos_b < text.len() {
        let m = match GAZETTEER_RE.find_from_pos(text, pos_b) {
            Ok(Some(m)) => m,
            _ => break,
        };
        let (sb, eb) = (m.start(), m.end());
        let sc = byte_to_char(text, sb);
        let ec = byte_to_char(text, eb);
        let low = crate::detectors::base::char_to_byte(text, sc.saturating_sub(SENSITIVE_WINDOW_CHARS));
        let high = crate::detectors::base::char_to_byte(text, (ec + SENSITIVE_WINDOW_CHARS).min(total_chars));
        if SENSITIVE_RE.is_match(&text[low..high]).unwrap_or(false) {
            push_match(out, seen, text, sb, eb);
        }
        pos_b = if eb > sb { eb } else { sb + 1 };
    }
}

/// Pass 3 — person list: items must be title-prefixed or gazetteer names.
fn detect_list(text: &str, out: &mut Vec<RawMatch>, seen: &mut HashSet<(usize, usize)>) {
    if !LIST_CONTEXT_RE.is_match(text).unwrap_or(false) {
        return;
    }
    let mut pos_b = 0usize;
    while pos_b <= text.len() {
        let caps = match LIST_ITEM_RE.captures_from_pos(text, pos_b) {
            Ok(Some(c)) => c,
            _ => break,
        };
        let whole = caps.get(0).unwrap();
        let name = caps.name("name").unwrap();
        let pre = caps.name("pre");
        if pre.is_some() || GAZETTEER.contains(&name.as_str()) {
            let sb = pre.map(|p| p.start()).unwrap_or(name.start());
            push_match(out, seen, text, sb, name.end());
        }
        pos_b = if whole.end() > whole.start() { whole.end() } else { whole.start() + 1 };
    }
}

pub struct ThaiNameNoPrefixDetector;

impl Detector for ThaiNameNoPrefixDetector {
    fn name(&self) -> &'static str {
        "thai_name_no_prefix"
    }
    fn entity_type(&self) -> &'static str {
        "thai_name"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }
    fn priority(&self) -> i32 {
        -5
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out: Vec<RawMatch> = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        let total_chars = text.chars().count();

        // Byte cursor. Search from text[pos_byte..] each iteration.
        let mut pos_byte = 0usize;
        let n_bytes = text.len();

        while pos_byte < n_bytes {
            // Search the FULL text from a byte position (Python
            // ``_NAME_RE.search(text, pos)``), so the ``(?<!THAI)`` lookbehind
            // can see the character before the cursor. Slicing the text
            // instead (the old port) blinded the lookbehind after a
            // one-codepoint reject step, and the next candidate could start
            // MID-WORD (``บอร์ ติดต่อกลับ`` from ``เบอร์ ติดต่อกลับ``).
            let captures = match NAME_RE.captures_from_pos(text, pos_byte) {
                Ok(Some(c)) => c,
                _ => break,
            };
            let g1 = match captures.get(1) {
                Some(g) => g,
                None => break,
            };
            let abs_start_b = g1.start();
            let abs_end_b = g1.end();

            // Context window in CHARACTERS, outside the match span.
            let abs_start_char = byte_to_char(text, abs_start_b);
            let abs_end_char = byte_to_char(text, abs_end_b);
            let low_char = abs_start_char.saturating_sub(CONTEXT_WINDOW_CHARS);
            let high_char = (abs_end_char + CONTEXT_WINDOW_CHARS).min(total_chars);
            let low_b = crate::detectors::base::char_to_byte(text, low_char);
            let high_b = crate::detectors::base::char_to_byte(text, high_char);

            let before = &text[low_b..abs_start_b];
            let mut outside = String::new();
            outside.push_str(before);
            outside.push_str(&text[abs_end_b..high_b]);

            let context_ok = CONTEXT_RE.is_match(&outside).unwrap_or(false);

            // One-codepoint step used on every reject (Python ``pos = start + 1``).
            let reject_step = abs_start_b
                + text[abs_start_b..]
                    .chars()
                    .next()
                    .map(|c| c.len_utf8())
                    .unwrap_or(1);

            if !context_ok {
                pos_byte = reject_step;
                continue;
            }

            let pair = g1.as_str();
            let mut parts = pair.splitn(2, char::is_whitespace);
            let w1 = parts.next().unwrap_or("");
            let w2 = parts.next().unwrap_or("").trim_start();

            if TRAIL_LABELS.contains(&w2) {
                // FIX26: the second token is a field label, never a surname.
                let label_before = NAME_LABEL_BEFORE_RE.is_match(before).unwrap_or(false);
                if !is_stop_token(w1) && label_before {
                    let w1_end_b = abs_start_b + w1.len();
                    let w1_end_char = byte_to_char(text, w1_end_b);
                    if seen.insert((abs_start_char, w1_end_char)) {
                        out.push(RawMatch {
                            start: abs_start_char,
                            end: w1_end_char,
                            matched_text: w1.to_string(),
                            confidence: 1.0,
                            priority: -5,
                            entity_type: None,
                        });
                    }
                    pos_byte = w1_end_b;
                } else {
                    pos_byte = reject_step;
                }
            } else if is_stop_token(w1) || is_stop_token(w2) {
                pos_byte = reject_step;
            } else {
                if seen.insert((abs_start_char, abs_end_char)) {
                    out.push(RawMatch {
                        start: abs_start_char,
                        end: abs_end_char,
                        matched_text: pair.to_string(),
                        confidence: 1.0,
                        priority: -5,
                        entity_type: None,
                    });
                }
                pos_byte = abs_end_b;
            }
        }
        // FIX27 Part 3 — Python runs the gazetteer and list passes after Pass 1,
        // deduplicated on (start, end).
        detect_gazetteer(text, &mut out, &mut seen);
        detect_list(text, &mut out, &mut seen);
        out
    }
}
