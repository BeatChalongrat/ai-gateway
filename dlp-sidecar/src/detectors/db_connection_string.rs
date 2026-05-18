//! Database connection-string detector — port of ``db_connection_string.py``.
//!
//! Regex unchanged from Python; ``[^@\s/?#:]*:[^@\s/?#]+`` requires
//! a colon-separated userinfo with non-empty password so a bare
//! ``postgresql://host/db`` (no creds) doesn't match.

use fancy_regex::Regex;
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static CONN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://[^@\s/?#:]*:[^@\s/?#]+@[^\s/?#]+(?:[:/][^\s?#]*)?",
    )
    .expect("db conn regex")
});

pub struct DbConnectionStringDetector;

impl Detector for DbConnectionStringDetector {
    fn name(&self) -> &'static str {
        "db_connection_string"
    }
    fn entity_type(&self) -> &'static str {
        "db_connection_string"
    }
    fn category(&self) -> &'static str {
        "credential"
    }
    fn severity(&self) -> u32 {
        4 // CRITICAL
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in CONN_RE.find_iter(text).flatten() {
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
            });
        }
        out
    }
}
