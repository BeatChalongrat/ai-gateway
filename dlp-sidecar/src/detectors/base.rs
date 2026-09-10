//! Detector trait — mirrors ``app.services.dlp.detectors.base.BaseDetector``.
//!
//! Every Rust detector implements ``Detector::detect`` returning a
//! flat list of :struct:`RawMatch`. The parity test (and eventually
//! the gRPC server) wraps each match with the metadata pulled from
//! the trait methods (entity_type / category / severity / priority).
//!
//! Offsets in ``RawMatch`` are CHARACTER (code-point) offsets — same
//! semantics as Python's ``re.finditer``. Rust's regex crates return
//! byte offsets, so each detector calls :fn:`byte_to_char` before
//! emitting a match. For pure-ASCII inputs the conversion is a
//! no-op; for multi-byte inputs (Thai, emoji) it is load-bearing.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RawMatch {
    pub start: usize,
    pub end: usize,
    pub matched_text: String,
    pub confidence: f32,
    pub priority: i32,
    /// Per-match entity_type override. ``None`` → the server applies
    /// the detector's uniform :fn:`Detector::entity_type`. ``Some`` →
    /// this match carries a distinct entity_type, mirroring Python
    /// detectors that emit per-pattern types (``api_key_secret`` →
    /// anthropic_api_key/github_pat/…; ``password_field`` →
    /// password_in_config/password_in_text). Load-bearing for the
    /// classifier's ``len(entity_types) >= 3`` risk escalation.
    #[serde(default)]
    pub entity_type: Option<&'static str>,
}

pub trait Detector: Send + Sync {
    fn name(&self) -> &'static str;
    fn entity_type(&self) -> &'static str;
    fn category(&self) -> &'static str;
    /// 0=NONE 1=LOW 2=MEDIUM 3=HIGH 4=CRITICAL
    fn severity(&self) -> u32;
    fn priority(&self) -> i32 {
        0
    }
    fn detect(&self, text: &str) -> Vec<RawMatch>;

    /// Context-aware detect. Default ignores ``from_decoded`` and
    /// delegates to :fn:`detect`. Detectors that relax thresholds on
    /// base64-decoded content override this — mirrors the Python
    /// ``from_decoded`` kwarg gated on ``ACCEPTS_DECODED`` (phone drops
    /// the context-keyword requirement; api_key_secret runs the extra
    /// short-key ``_DECODED_PATTERNS``). The gRPC server calls this with
    /// ``ScanRequest.from_decoded`` so a decoded-segment scan relaxes
    /// identically across engines.
    fn detect_ctx(&self, text: &str, _from_decoded: bool) -> Vec<RawMatch> {
        self.detect(text)
    }
}

/// Convert a byte offset into a character (code-point) offset.
///
/// Rust regex crates index by byte; Python ``re`` indexes by code
/// point. The two diverge once the input contains a multi-byte
/// character before the match start. We pay the conversion cost on
/// every emitted match because the parity contract is keyed on
/// Python-style offsets — operator dashboards and the masker both
/// rely on those being identical across engines.
pub fn byte_to_char(text: &str, byte_offset: usize) -> usize {
    if byte_offset == 0 {
        return 0;
    }
    if byte_offset >= text.len() {
        return text.chars().count();
    }
    text[..byte_offset].chars().count()
}

/// Convert a CHARACTER (code-point) offset to a byte offset.
///
/// Mirror of :fn:`byte_to_char`. Used when we want to slice the
/// original string by Python-style codepoint window bounds — the
/// context-window helper below uses it to size the ``[lo..hi]``
/// slice it scans for keywords.
pub fn char_to_byte(text: &str, char_offset: usize) -> usize {
    if char_offset == 0 {
        return 0;
    }
    let mut byte = text.len();
    for (i, (b, _)) in text.char_indices().enumerate() {
        if i == char_offset {
            byte = b;
            break;
        }
    }
    byte
}

/// True iff one of ``keywords`` appears (case-insensitively) in the
/// character window ``[start - radius, end + radius)`` of ``text``.
///
/// ``start`` / ``end`` are CHARACTER offsets so the helper composes
/// cleanly with each detector's regex result. ``keywords`` are
/// matched as plain ASCII substrings — Thai keywords need no
/// special handling because the input has already been NFKC-
/// normalised upstream and Thai letter sequences are unique.
///
/// This is intentionally a substring contains check rather than a
/// regex pass: the Python detectors that ship a keyword *list*
/// (medical_record, biometric_data, religion_belief, ...) use the
/// same plain substring semantics under the hood. Detectors that
/// need word boundaries (medical_record's `\b(?:mrn|hn|...)\b`)
/// embed that into the regex directly and bypass this helper.
pub fn has_context_keyword(
    text: &str,
    start: usize,
    end: usize,
    radius: usize,
    keywords: &[&str],
) -> bool {
    let total_chars = text.chars().count();
    let lo = start.saturating_sub(radius);
    let hi = (end + radius).min(total_chars);
    let lo_b = char_to_byte(text, lo);
    let hi_b = char_to_byte(text, hi);
    let window = &text[lo_b..hi_b];
    let window_lower = window.to_lowercase();
    keywords
        .iter()
        .any(|kw| window_lower.contains(&kw.to_lowercase()))
}

// ── FIX103 — a regex engine error is never silent ────────────────────────
//
// fancy-regex's backtracking VM stops at its backtrack limit and returns an
// error, and its match iterators return that SAME error again on every
// `next()` without advancing. Every detector consumed them with
// `.flatten()`, which discards errors, so a large enough input — about
// 37,000 ASCII characters for `religion_belief` — turned one scan into a
// loop that never ends: the 100 % CPU wedge of FIX101 and FIX102.
// `is_match(..).unwrap_or(false)` and `Ok(Some(m)) => m, _ => break` were
// silent too: they did not spin, they just stopped looking.
//
// Now an engine error ends that iteration and is recorded on this thread.
// The server takes it after every detector and refuses the whole scan, so
// the gateway falls back to its Python engine, which has no backtrack limit
// and scans the same text in well under a second (0.305 s for FIX102's
// 63,357-character prompt). No span is skipped and nothing is chunked.

use std::cell::RefCell;

thread_local! {
    static ENGINE_ERROR: RefCell<Option<String>> = const { RefCell::new(None) };
}

/// Record a regex engine error for the detector running on this thread.
/// The first error wins; later ones in the same detector add nothing.
pub fn record_engine_error(err: &fancy_regex::Error) {
    ENGINE_ERROR.with(|slot| {
        let mut slot = slot.borrow_mut();
        if slot.is_none() {
            *slot = Some(err.to_string());
        }
    });
}

/// Take and clear the engine error recorded on this thread, if any.
pub fn take_engine_error() -> Option<String> {
    ENGINE_ERROR.with(|slot| slot.borrow_mut().take())
}

fn ok_or_record<T>(r: Result<T, fancy_regex::Error>) -> Option<T> {
    match r {
        Ok(v) => Some(v),
        Err(e) => {
            record_engine_error(&e);
            None
        }
    }
}

/// `.engine_ok()` — consume a fancy-regex match iterator, stopping at the
/// first engine error (recorded) instead of skipping it forever.
pub trait EngineOk<T>: Iterator<Item = Result<T, fancy_regex::Error>> + Sized {
    #[allow(clippy::type_complexity)]
    fn engine_ok(self) -> std::iter::MapWhile<Self, fn(Result<T, fancy_regex::Error>) -> Option<T>> {
        self.map_while(ok_or_record::<T> as fn(Result<T, fancy_regex::Error>) -> Option<T>)
    }
}

impl<T, I: Iterator<Item = Result<T, fancy_regex::Error>>> EngineOk<T> for I {}

/// `.engine_or(default)` — one fancy-regex call; an engine error is recorded
/// and `default` returned.
pub trait EngineResult<T> {
    fn engine_or(self, default: T) -> T;
}

impl<T> EngineResult<T> for Result<T, fancy_regex::Error> {
    fn engine_or(self, default: T) -> T {
        match self {
            Ok(v) => v,
            Err(e) => {
                record_engine_error(&e);
                default
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_errors_are_recorded_once_and_taken_once() {
        let _ = take_engine_error();
        let err = fancy_regex::Error::RuntimeError(fancy_regex::RuntimeError::BacktrackLimitExceeded);
        assert!(!Err::<bool, _>(err).engine_or(false));
        assert!(take_engine_error().is_some());
        assert!(take_engine_error().is_none(), "taking clears the slot");
        let ok: Result<bool, fancy_regex::Error> = Ok(true);
        assert!(ok.engine_or(false) && take_engine_error().is_none());
    }

    #[test]
    fn byte_to_char_ascii_is_identity() {
        assert_eq!(byte_to_char("hello world", 6), 6);
    }

    #[test]
    fn byte_to_char_thai_diverges() {
        // "abc" + 3 Thai chars (3 bytes each in UTF-8) + space + "x".
        // Byte offsets: a=0 b=1 c=2 น=3-5 า=6-8 น=9-11 ' '=12 x=13.
        // Char offsets: a=0 b=1 c=2 น=3   า=4   น=5   ' '=6  x=7.
        let text = "abcนาน x";
        let byte_x = text.find('x').unwrap();
        assert_eq!(byte_x, 13);
        assert_eq!(byte_to_char(text, byte_x), 7);
    }

    #[test]
    fn has_context_keyword_finds_thai() {
        // Thai keyword "โทร" within the window.
        let text = "โทร 0812345678 ครับ";
        let chars: Vec<char> = text.chars().collect();
        let dig_start = chars.iter().position(|c| *c == '0').unwrap();
        let dig_end = dig_start + 10;
        let _ = dig_end;
        assert!(has_context_keyword(text, dig_start, dig_start + 10, 25, &["โทร"]));
    }

    #[test]
    fn has_context_keyword_misses_when_out_of_window() {
        let text = "phone:                                          0812345678";
        // keyword far from digits — should not match with tight radius
        let dig_start = text.find('0').unwrap();
        let dig_end = dig_start + 10;
        let chars_before = text[..dig_start].chars().count();
        assert!(!has_context_keyword(
            text,
            chars_before,
            chars_before + 10,
            5,  // tight radius
            &["phone"]
        ));
    }
}
