//! FIX29 — Thai given-name gazetteer with a segmentation-aware right
//! boundary. Port of ``thai_gazetteer.py`` — same two lexicons embedded at
//! build time (``dlp-sidecar/data``, byte-identical to the Python copies; a
//! Python test compares hashes), same rules, same spans:
//!
//!   * a candidate starts at a left boundary (start of text or a non-Thai
//!     char before it) and is the LONGEST gazetteer name there;
//!   * its right edge is a word edge: end of text, a non-Thai char, or the
//!     start of a dictionary word (``ณัฐพล|บอก``);
//!   * a candidate that continues into a longer dictionary word is rejected.
//!
//! Names lexicon: Thai Male and Female Names Corpus (Korkeat Wannapat,
//! CC BY-SA 4.0, via pythainlp 5.3.7), ≥3 codepoints, dictionary words
//! removed. Word list: pythainlp ``words_th`` (CC0 1.0). Offsets are CHAR
//! offsets (the RawMatch contract).

use once_cell::sync::Lazy;
use std::collections::HashSet;

const NAMES_TXT: &str = include_str!("../../data/thai_given_names.txt");
const WORDS_TXT: &str = include_str!("../../data/thai_words.txt");
// Names that are ALSO dictionary words — out of NAMES (they fire on prose),
// valid for trim_run (after a title the text is a name by construction).
const NAMES_AMBIGUOUS_TXT: &str = include_str!("../../data/thai_given_names_ambiguous.txt");

pub const NAME_MIN: usize = 3;
pub const NAME_MAX: usize = 20;
const WORD_MAX: usize = 20;
const LONGER_MAX: usize = 5;

static NAMES: Lazy<HashSet<&'static str>> =
    Lazy::new(|| NAMES_TXT.lines().map(str::trim).filter(|l| !l.is_empty()).collect());
static NAMES_AMBIGUOUS: Lazy<HashSet<&'static str>> =
    Lazy::new(|| NAMES_AMBIGUOUS_TXT.lines().map(str::trim).filter(|l| !l.is_empty()).collect());
static WORDS: Lazy<HashSet<&'static str>> =
    Lazy::new(|| WORDS_TXT.lines().map(str::trim).filter(|l| !l.is_empty()).collect());

pub fn is_thai(c: char) -> bool {
    ('\u{0E01}'..='\u{0E5B}').contains(&c)
}

/// Text as chars plus each char's byte offset (with the end sentinel).
pub struct Chars<'a> {
    text: &'a str,
    chars: Vec<char>,
    byte_at: Vec<usize>,
}

impl<'a> Chars<'a> {
    pub fn new(text: &'a str) -> Self {
        let mut chars = Vec::with_capacity(text.len());
        let mut byte_at = Vec::with_capacity(text.len() + 1);
        for (b, c) in text.char_indices() {
            chars.push(c);
            byte_at.push(b);
        }
        byte_at.push(text.len());
        Chars { text, chars, byte_at }
    }
    pub fn len(&self) -> usize {
        self.chars.len()
    }
    pub fn slice(&self, s: usize, e: usize) -> &'a str {
        &self.text[self.byte_at[s]..self.byte_at[e]]
    }
    pub fn byte_of(&self, i: usize) -> usize {
        self.byte_at[i]
    }
    pub fn thai_at(&self, i: usize) -> bool {
        i < self.chars.len() && is_thai(self.chars[i])
    }
}

pub fn word_starts_at(t: &Chars, i: usize) -> bool {
    let max = WORD_MAX.min(t.len().saturating_sub(i));
    let mut len = max;
    while len >= 2 {
        if WORDS.contains(t.slice(i, i + len)) {
            return true;
        }
        len -= 1;
    }
    false
}

pub fn right_edge_ok(t: &Chars, end: usize) -> bool {
    end >= t.len() || !t.thai_at(end) || word_starts_at(t, end)
}

fn continues_into_word(t: &Chars, start: usize, end: usize) -> bool {
    for k in 1..=LONGER_MAX {
        if end + k <= t.len() && WORDS.contains(t.slice(start, end + k)) {
            return true;
        }
    }
    false
}

/// Length (chars) of the longest gazetteer name at ``i`` whose right edge is a word edge.
pub fn name_at(t: &Chars, i: usize) -> Option<usize> {
    if NAMES.is_empty() {
        return None;
    }
    let max = NAME_MAX.min(t.len().saturating_sub(i));
    let mut len = max;
    while len >= NAME_MIN {
        if NAMES.contains(t.slice(i, i + len)) {
            let end = i + len;
            if right_edge_ok(t, end) && !continues_into_word(t, i, end) {
                return Some(len);
            }
        }
        len -= 1;
    }
    None
}

/// A name label glued to the name (``ลูกค้าชื่อสมชาย``) is a left boundary too —
/// the commonest way a name enters a Thai sentence. Mirrors Python NAME_LABELS.
const NAME_LABELS: &[&str] = &["ชื่อ", "นามว่า", "ชื่อว่า"];

fn left_boundary(t: &Chars, i: usize) -> bool {
    if i == 0 || !t.thai_at(i - 1) {
        return true;
    }
    NAME_LABELS.iter().any(|lbl| {
        let n = lbl.chars().count();
        i >= n && t.slice(i - n, i) == *lbl
    })
}

/// (start, end) CHAR spans of gazetteer names at left word boundaries.
pub fn find_names(text: &str) -> Vec<(usize, usize)> {
    let t = Chars::new(text);
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < t.len() {
        if t.thai_at(i) && left_boundary(&t, i) {
            if let Some(len) = name_at(&t, i) {
                out.push((i, i + len));
                i += len;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// For a Thai run ``[start, end)`` (char offsets) that is NOT itself a name:
/// if it begins with a gazetteer name and the remainder begins a dictionary
/// word (``มีชัย|จอง…``), return the name's end; otherwise ``end``.
pub fn trim_run(t: &Chars, start: usize, end: usize) -> usize {
    let is_name = |s: &str| NAMES.contains(s) || NAMES_AMBIGUOUS.contains(s);
    if NAMES.is_empty() || is_name(t.slice(start, end)) {
        return end;
    }
    let max = NAME_MAX.min(end - start);
    let mut len = max;
    while len >= NAME_MIN {
        if is_name(t.slice(start, start + len)) && word_starts_at(t, start + len) {
            return start + len;
        }
        len -= 1;
    }
    end
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn glued_name_ends_at_the_name() {
        assert_eq!(find_names("ณัฐพลบอกว่าจะมาสาย"), vec![(0, "ณัฐพล".chars().count())]);
    }

    #[test]
    fn label_glued_to_the_name_is_a_left_boundary() {
        let text = "ลูกค้าชื่อสมชาย เบอร์";
        let s = "ลูกค้าชื่อ".chars().count();
        assert_eq!(find_names(text), vec![(s, s + "สมชาย".chars().count())]);
    }

    #[test]
    fn dictionary_word_is_not_a_name() {
        assert!(find_names("บอกชื่อสัตว์มา 1 ชนิด ตอบสั้นๆ").is_empty());
    }

    #[test]
    fn titled_run_is_trimmed_to_the_name() {
        let t = Chars::new("คุณมีชัยจองห้องประชุมไว้");
        // run = everything after คุณ (3 chars)
        assert_eq!(trim_run(&t, 3, t.len()), 3 + "มีชัย".chars().count());
    }
}
