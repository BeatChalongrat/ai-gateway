//! IPv4 + IPv6 detector — port of ``ip_address.py``.
//!
//! Lookbehind / lookahead anchors are load-bearing (the `regex`
//! crate doesn't support them — that's why this whole module uses
//! ``fancy-regex``). Patterns are byte-identical to Python.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static IPV4_RE: Lazy<Regex> = Lazy::new(|| {
    // (?<!\d) <octet>.<octet>.<octet>.<octet> (?!\d)
    let octet = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";
    let pat = format!(r"(?<!\d)(?:{octet}\.){{3}}{octet}(?!\d)");
    Regex::new(&pat).expect("ipv4 regex")
});

static IPV6_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?<![0-9A-Fa-f:])",
        r"(?:",
        r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}",
        r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:",
        r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}",
        r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}",
        r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}",
        r"|::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}",
        r")",
        r"(?![0-9A-Fa-f:])"
    ))
    .expect("ipv6 regex")
});

pub struct Ipv4Detector;
pub struct Ipv6Detector;

impl Detector for Ipv4Detector {
    fn name(&self) -> &'static str {
        "ip_address"
    }
    fn entity_type(&self) -> &'static str {
        "ipv4_address"
    }
    fn category(&self) -> &'static str {
        "network"
    }
    fn severity(&self) -> u32 {
        2  // MEDIUM
    }
    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in IPV4_RE.find_iter(text).flatten() {
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}

impl Detector for Ipv6Detector {
    fn name(&self) -> &'static str {
        "ip_address"
    }
    fn entity_type(&self) -> &'static str {
        "ipv6_address"
    }
    fn category(&self) -> &'static str {
        "network"
    }
    fn severity(&self) -> u32 {
        2
    }
    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in IPV6_RE.find_iter(text).flatten() {
            let value = m.as_str();
            // Python post-filter: require at least one ':' to drop
            // edge-case hex runs the regex can accept.
            if !value.contains(':') {
                continue;
            }
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: value.to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
