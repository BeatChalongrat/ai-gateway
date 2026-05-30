//! Password-in-config detector — port of ``password_field.py``.
//!
//! ``(?i)\b<label>\s*[:=]\s*(?P<value>"..."|'...'|\S+)``
//!
//! The detector emits start/end of the *value group only*, not the
//! whole label+value match. That contract is load-bearing for the
//! masker — it replaces the value while leaving the label visible.

use fancy_regex::Regex;
use once_cell::sync::Lazy;
use std::collections::HashSet;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static PASSWORD_RE: Lazy<Regex> = Lazy::new(|| {
    let label_alts =
        r"(?:password|passwd|pwd|secret_?key|api_?secret|auth_?token|db_?password)";
    let pat = format!(
        r"(?i)\b{label}\s*[:=]\s*(?P<value>\x22[^\x22]+\x22|'[^']+'|\S+)",
        label = label_alts
    );
    Regex::new(&pat).expect("password regex")
});

// Thai prose form — ``รหัสผ่านคือ P@ssw0rd123!`` etc. No (?i), no \b
// (Thai has no ASCII word boundary). Port of password_field.py _THAI_PASSWORD_RE.
static THAI_PASSWORD_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:รหัสผ่าน|พาสเวิร์ด)\s*(?:คือ|ว่า|=|:)?\s*(?P<value>\S{6,})")
        .expect("thai password regex")
});

// English no-separator prose form — "... password admin123". Double
// lookahead entropy gate: value >= 8 non-space chars AND >= 1 digit/special.
// Port of password_field.py _PASSWORD_NOSEP_RE.
static PASSWORD_NOSEP_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\bpassword\s+(?P<value>(?=\S{8,})(?=\S*[\d!@#$%^&*+\-=_./\\<>?,;:])\S+)",
    )
    .expect("password nosep regex")
});

pub struct PasswordFieldDetector;

impl Detector for PasswordFieldDetector {
    fn name(&self) -> &'static str {
        "password_field"
    }
    fn entity_type(&self) -> &'static str {
        "password_in_config"
    }
    fn category(&self) -> &'static str {
        "secret"
    }
    fn severity(&self) -> u32 {
        3  // HIGH
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        // Cross-pattern dedup on the value (start,end), first writer wins —
        // mirrors Python password_field.py's ``seen`` set across all 3 patterns.
        let mut seen: HashSet<(usize, usize)> = HashSet::new();
        // Per-pattern entity_type — the config label:value form emits
        // ``password_in_config``; the two prose forms emit
        // ``password_in_text`` (mirrors password_field.py's _emit calls).
        for (re, et) in [
            (&*PASSWORD_RE, "password_in_config"),
            (&*THAI_PASSWORD_RE, "password_in_text"),
            (&*PASSWORD_NOSEP_RE, "password_in_text"),
        ] {
            for caps in re.captures_iter(text).flatten() {
                let g = match caps.name("value") {
                    Some(g) => g,
                    None => continue,
                };
                let s = byte_to_char(text, g.start());
                let e = byte_to_char(text, g.end());
                if seen.insert((s, e)) {
                    out.push(RawMatch {
                        start: s,
                        end: e,
                        matched_text: g.as_str().to_string(),
                        confidence: 1.0,
                        priority: 0,
                        entity_type: Some(et),
                    });
                }
            }
        }
        out
    }
}
