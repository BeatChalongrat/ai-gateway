//! Date-of-birth detector — port of ``date_of_birth.py``.
//!
//! Single regex with named ``value`` group. The Thai keywords are
//! preceded by ``(?<![฀-๿])`` so "เกิด" doesn't match as a substring
//! of compound Thai words.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static DOB_RE: Lazy<Regex> = Lazy::new(|| {
    // Mirror the Python pattern verbatim. Months / Thai months /
    // Thai short months are inlined for clarity.
    let pat = concat!(
        r"(?i)(?:",
        r"born|d\.?o\.?b\.?|date\s*of\s*birth|birthday|birthdate",
        // Person-role-noun + DOB compound branch (port of dob.py T15
        // audit fix). Listed before the bare-เกิด lookbehind branch so
        // role-noun forms recover without re-introducing event-noun FPs.
        r"|(?:ลูกค้า|ผู้ป่วย|คนไข้|สมาชิก|พนักงาน|ผู้ใช้)\s*(?:วันเกิด|เกิดวันที่)",
        r"|(?<![฀-๿])(?:วันเกิด|เกิดวันที่|เกิด)",
        r")",
        r"(?:\s+(?:on|in|at|is|was|=|:))?",
        r"[\s:=\-]{0,5}",
        r"(?P<value>",
        // DD/MM/YYYY etc.
        r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}",
        // YYYY-MM-DD ISO
        r"|\d{4}-\d{1,2}-\d{1,2}",
        // English Month forms
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*|\s+)\d{4}",
        r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}",
        // Thai month full
        r"|\d{1,2}\s+(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+\d{4}",
        // Thai month short
        r"|\d{1,2}\s+(?:ม\.?ค\.?|ก\.?พ\.?|มี\.?ค\.?|เม\.?ย\.?|พ\.?ค\.?|มิ\.?ย\.?|ก\.?ค\.?|ส\.?ค\.?|ก\.?ย\.?|ต\.?ค\.?|พ\.?ย\.?|ธ\.?ค\.?)\s+\d{2,4}",
        r")",
    );
    Regex::new(pat).expect("dob regex")
});

pub struct DateOfBirthDetector;

impl Detector for DateOfBirthDetector {
    fn name(&self) -> &'static str {
        "date_of_birth"
    }
    fn entity_type(&self) -> &'static str {
        "date_of_birth"
    }
    fn category(&self) -> &'static str {
        "pii"
    }
    fn severity(&self) -> u32 {
        3 // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for caps in DOB_RE.captures_iter(text).flatten() {
            if let Some(g) = caps.name("value") {
                out.push(RawMatch {
                    start: byte_to_char(text, g.start()),
                    end: byte_to_char(text, g.end()),
                    matched_text: g.as_str().to_string(),
                    confidence: 1.0,
                    priority: 0,
                    entity_type: None,
                });
            }
        }
        out
    }
}
