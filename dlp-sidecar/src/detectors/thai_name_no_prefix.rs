//! Fallback Thai-name detector (no title prefix) — port of
//! ``thai_name_no_prefix.py`` Pass 1 (two-word + context keyword).
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
        out
    }
}
