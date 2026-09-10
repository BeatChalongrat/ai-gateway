//! Thai vehicle plate detector — port of ``vehicle_plate.py``.
//!
//! Two patterns: (a) keyword prefix → plate; (b) plate → province.
//! Both use a named ``value`` group; matches are deduped by span.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

const PROVINCES: &str = concat!(
    "กรุงเทพ|กรุงเทพมหานคร|นนทบุรี|เชียงใหม่|ภูเก็ต|ขอนแก่น|ชลบุรี|",
    "นครราชสีมา|สงขลา|อุดรธานี|พิษณุโลก|อยุธยา|ระยอง|สมุทรปราการ|",
    "Bangkok|Nonthaburi|Chiang\\s*Mai|Phuket|Khon\\s*Kaen|Chonburi|",
    "Nakhon\\s*Ratchasima|Songkhla|Udon\\s*Thani|Phitsanulok|Ayutthaya|",
    "Rayong|Samut\\s*Prakan",
);

const PLATE_BODY: &str = r"(?:[\u{0E01}-\u{0E2E}]{1,2}|[A-Z]{1,2})[ \-]?\d{1,4}";

static KEYWORD_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(
        r"(?i)(?:ทะเบียน(?:รถ)?|license\s*plate|plate\s*(?:no\.?|number|#)?)[^A-Za-z0-9\u{{0E00}}-\u{{0E7F}}]{{0,10}}(?P<value>{})",
        PLATE_BODY,
    );
    Regex::new(&pat).expect("plate keyword")
});

static PROVINCE_RE: Lazy<Regex> = Lazy::new(|| {
    let pat = format!(r"(?i)(?P<value>{})\s*(?:{})", PLATE_BODY, PROVINCES);
    Regex::new(&pat).expect("plate province")
});

pub struct VehiclePlateDetector;

impl Detector for VehiclePlateDetector {
    fn name(&self) -> &'static str {
        "vehicle_plate"
    }
    fn entity_type(&self) -> &'static str {
        "vehicle_plate"
    }
    fn category(&self) -> &'static str {
        "identity"
    }
    fn severity(&self) -> u32 {
        2 // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        for re in [&*KEYWORD_RE, &*PROVINCE_RE] {
            for caps in re.captures_iter(text).engine_ok() {
                if let Some(g) = caps.name("value") {
                    let s = byte_to_char(text, g.start());
                    let e = byte_to_char(text, g.end());
                    if seen.insert((s, e)) {
                        out.push(RawMatch {
                            start: s,
                            end: e,
                            matched_text: g.as_str().to_string(),
                            confidence: 1.0,
                            priority: 0,
                            entity_type: None,
                        });
                    }
                }
            }
        }
        out
    }
}
