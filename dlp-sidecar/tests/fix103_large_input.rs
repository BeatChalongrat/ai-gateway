//! FIX103 Part C — a large input must never wedge the sidecar.
//!
//! The DLP sidecar wedged at ~100 % CPU three rounds running (FIX101,
//! FIX102 twice), each time on a large compaction/extraction request.
//! Characterised against a private instance: the real FIX102 prompt scans
//! in 130 ms at 32,000 characters and never finishes at 40,000; alone,
//! `religion_belief` takes 37 ms at 36,000 ASCII characters and never
//! finishes at 40,000, while 40,000 Thai characters take 41 ms. A cliff
//! from milliseconds to "never" is not a slow regex.
//!
//! The mechanism: fancy-regex's backtracking VM gives up after its
//! backtrack limit and returns an error, and its match iterators return
//! that error again from the same position on every `next()`. Every
//! detector consumed those iterators with `.flatten()`, which silently
//! discards errors — so the loop asks forever.

use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use dlp_sidecar::detectors::base::take_engine_error;
use dlp_sidecar::registry;

/// Deterministic English word salad: no digits, no punctuation that any
/// detector keys on — the shape of a folded conversation, not an attack.
fn ascii_words(n: usize) -> String {
    const VOCAB: &[&str] = &[
        "budget", "alert", "database", "review", "latency", "migration", "outage", "rollback",
        "schema", "index", "cache", "queue", "retry", "timeout", "partition", "replica", "backup",
        "restore", "audit", "metric", "dashboard", "incident", "escalation", "runbook", "deploy",
    ];
    let mut s = String::with_capacity(n + 16);
    let mut x: u64 = 7;
    while s.len() < n {
        x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        s.push_str(VOCAB[(x >> 33) as usize % VOCAB.len()]);
        s.push(if (x >> 20) % 9 == 0 { '\n' } else { ' ' });
    }
    s.truncate(n);
    s
}

fn digits(n: usize) -> String {
    let mut s = String::with_capacity(n + 64);
    let mut i: u64 = 1000;
    while s.len() < n {
        i = i.wrapping_mul(2862933555777941757).wrapping_add(3037000493);
        s.push_str(&format!("order {} amount {} ref #{}. ", i % 999_983, i % 99_991, i % 9_973));
    }
    s.truncate(n);
    s
}

fn thai(chars: usize) -> String {
    let para = "การออกแบบระบบแจ้งเตือนควรเริ่มจากการวิเคราะห์เหตุการณ์ที่ต้องแจ้ง กำหนดระดับความรุนแรง และช่องทางการส่งที่เหมาะสมกับผู้รับแต่ละกลุ่ม ";
    para.chars().cycle().take(chars).collect()
}

/// The religion_belief keyword pattern, verbatim from
/// src/detectors/religion_belief.rs — the one that wedged FIX102's fold.
const RELIGION_KEYWORDS: &str = concat!(
    r"(?i)(?:",
    r"นับถือ|ศาสนา|ศรัทธา|ละหมาด|สวดมนต์|",
    r"เข้าวัด|ถือศีล|บวช|มุสลิม|อิสลาม|พุทธ|คริสต์|ฮินดู|ซิกข์|",
    r"ยิว|ยูดาย์|",
    r"\b(?:my|his|her|their)\s+(?:religion|faith|belief)\b|",
    r"\b(?:I|he|she|they)\s+(?:am|is|are|was|were)\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)\b|",
    r"\bI'm\s+(?:an?\s+)?(?:practicing\s+|devout\s+|observant\s+)?(?:Buddhist|Muslim|Christian|Hindu|Sikh|Jewish|Catholic|Protestant|atheist|agnostic)\b|",
    r"\b(?:I|he|she|they)\s+(?:believes?|worships?|prays?|practices?)\b|",
    r"\b(?:I|he|she|they)\s+(?:attends?|attended|goe?s?\s+to|went\s+to|visits?|visited)\s+(?:the\s+)?(?:local\s+)?(?:mosque|church|synagogue|temple|gurudwara|chapel|cathedral)\b|",
    r"\b(?:practicing|devout|observant)\s+(?:Muslim|Christian|Buddhist|Hindu|Sikh|Jewish|Catholic|Protestant)\b|",
    r"\b(?:I|he|she|they)\s+(?:converted?\s+to|follows?)\b",
    r")",
);

/// Characterisation of the dependency, not of our code: if a fancy-regex
/// upgrade changes this, the guard in `detectors::base` may be removable —
/// read this test before deciding.
#[test]
fn fancy_regex_limit_error_repeats_from_the_same_position() {
    let re = fancy_regex::Regex::new(RELIGION_KEYWORDS).expect("pattern");
    let small = ascii_words(30_000);
    assert!(
        re.find_iter(&small).all(|r| r.is_ok()),
        "below the limit the iterator ends normally"
    );
    let large = ascii_words(40_000);
    let first: Vec<_> = re.find_iter(&large).take(5).collect();
    assert_eq!(first.len(), 5, "the iterator did not end");
    assert!(
        first.iter().all(|r| r.is_err()),
        "expected the same error five times in a row, got {:?}",
        first.iter().map(|r| r.is_ok()).collect::<Vec<_>>()
    );
}

/// Run `f` on another thread; `Err` if it has not finished in `secs`. A
/// spinning detector keeps its thread, but the test fails instead of
/// hanging the suite, and the process exits when the suite ends.
fn finishes_within<T: Send + 'static>(secs: u64, f: impl FnOnce() -> T + Send + 'static) -> Result<T, ()> {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let _ = tx.send(f());
    });
    rx.recv_timeout(Duration::from_secs(secs)).map_err(|_| ())
}

#[test]
fn a_detector_that_hits_the_engine_limit_says_so_instead_of_spinning() {
    let det = registry::default_detectors(true)
        .into_iter()
        .find(|d| d.name() == "religion_belief")
        .expect("religion_belief is registered");
    let text = ascii_words(40_000);
    let (_, err) = finishes_within(20, move || {
        let _ = take_engine_error();
        let n = det.detect(&text).len();
        (n, take_engine_error())
    })
    .expect("religion_belief spun on 40,000 ASCII characters");
    assert!(err.is_some(), "the engine error must be recorded, not swallowed");
}

#[test]
fn the_scan_is_refused_so_the_gateway_falls_back_instead_of_missing_a_mask() {
    let dets = registry::default_detectors(true);
    let text = ascii_words(40_000);
    let refused = finishes_within(60, move || registry::run_detectors(&text, &dets, &[], false))
        .expect("run_detectors did not finish");
    let reason = refused.expect_err("a scan with an engine error must be refused, not reported as clean");
    assert!(reason.starts_with("religion_belief"), "{reason}");

    // An ordinary message is still scanned in full, and its PII found.
    let dets = registry::default_detectors(true);
    let ok = registry::run_detectors("call 081-234-5678 or mail probe@example.com", &dets, &[], false)
        .expect("a small scan is not refused");
    let found: Vec<&str> = ok.iter().map(|(n, ..)| n.as_str()).collect();
    assert!(found.contains(&"email") && found.contains(&"phone"), "{found:?}");
}

#[test]
fn every_detector_finishes_on_large_inputs() {
    let inputs = [
        ("ascii words 200k", ascii_words(200_000)),
        ("digits 200k", digits(200_000)),
        ("thai 100k chars", thai(100_000)),
    ];
    let mut stuck = Vec::new();
    for (label, text) in inputs.iter() {
        for det in registry::default_detectors(true) {
            let name = det.name();
            let t = text.clone();
            if finishes_within(20, move || det.detect(&t).len()).is_err() {
                stuck.push(format!("{name} on {label}"));
            }
        }
    }
    assert!(stuck.is_empty(), "did not finish within 20 s: {stuck:?}");
}
