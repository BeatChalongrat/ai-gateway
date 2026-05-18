"""Detector accuracy audit (measurement, not a pass/fail gate).

Builds a small curated corpus per detector category and runs the
default ``DetectorRegistry`` against every sample. Counts:

  TP — sample listed under ``should_detect``     and at least one
        match of the right ``detector_name`` was produced.
  FN — sample listed under ``should_detect``     but the detector
        produced no match (the PII leaked past).
  FP — sample listed under ``should_not_detect`` but the detector
        fired anyway (noisy match).

Prints a single table to stdout at the end of the session — see it
with ``pytest -s tests/test_dlp_accuracy.py``. There are no
hard-coded numeric expectations; the values are informational and
land in ``tests/dlp_accuracy_report.md`` so we can track drift across
detector changes.

API note: the spec sketch used ``classifier.classify(text)``, but the
production API is ``DetectorRegistry.scan_text(text)`` returning a
list of ``DetectionMatch`` (the classifier operates on already-found
matches, not raw text). Each match carries ``detector_name`` —
that's what we group on below.
"""

from __future__ import annotations

import pytest

from app.services.dlp.detectors.registry import get_default_registry


# ``thai_national_id`` rejects checksum-invalid runs UNLESS preceded by
# a keyword. For the should_detect cases that don't use a keyword we
# need a real valid ID — generate one deterministically here so the
# test corpus never drifts away from a passing checksum.
def make_valid_thai_id(prefix: str = "310060087100") -> str:
    """Append the mod-11 check digit to a 12-digit prefix.

    The prefix must be exactly 12 numeric chars; the function does no
    other validation (this is a test helper, not a citizen check).
    """
    assert len(prefix) == 12 and prefix.isdigit()
    weights = (13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(int(prefix[i]) * weights[i] for i in range(12))
    check = (11 - (total % 11)) % 10
    return prefix + str(check)


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

# (detector_name, accepted_aliases) — ``accepted_aliases`` is the
# tuple of detector_name values that should count as a hit for this
# row. Some categories (Thai name, credential) are produced by more
# than one detector, so we accept any of them.
CASES: list[dict] = [
    {
        "label": "email",
        "names": ("email",),
        "should_detect": [
            "ส่งข้อมูลมาที่ john.doe@company.com ได้เลย",
            "contact me at user+tag@subdomain.example.co.th",
            "อีเมลผม: beat@sran.ai",
            "EMAIL: admin@internal.corp",
        ],
        "should_not_detect": [
            "version 2.0@release",
            "ไม่มีอีเมล",
            "price is $2.00 @ market",
        ],
    },
    {
        "label": "phone_number",
        "names": ("phone",),
        "should_detect": [
            "โทร 081-234-5678",
            "call me at +66812345678",
            "เบอร์: 02-555-1234",
            "ติดต่อ 0891234567",
            # New post-Fix-4 samples — Thai landlines (9 digits).
            "โทร 02-555-1234",
            "สำนักงาน 02 666 7890",
            "ติดต่อ 053-123-4567",
            "tel: 025551234",
        ],
        "should_not_detect": [
            "รหัส 1234567",
            "เวลา 08:12:34",
            "run:1776398778:25",     # epoch timestamp — known FP risk
            # New post-Fix-4: version strings starting with 02- must
            # not be masked as a Bangkok landline.
            "version 02-release",
        ],
    },
    {
        "label": "credit_card",
        "names": ("credit_card",),
        "should_detect": [
            "บัตร 4242 4242 4242 4242",
            "card number: 5500-0000-0000-0004",
            "Visa: 4111111111111111",
        ],
        "should_not_detect": [
            "รหัส 1234 5678 9012 3456 ไม่ใช่บัตร",
            "order #4242424242424242",
        ],
    },
    {
        "label": "thai_id",
        "names": ("thai_national_id",),
        "should_detect": [
            "เลขบัตร 1-1001-12345-67-8",
            "ID: 3100600871635",
            "บัตรประชาชน 1234567890123",
            # New post-Fix-2: keyword-context bypass for raw 13-digit
            # runs that don't pass checksum.
            "เลขบัตร 3100600871635",
            # Keyword + a CHECKSUM-VALID id (generated below).
            f"บัตรประชาชน: {make_valid_thai_id()}",
            "ID: 1100700171635",
        ],
        "should_not_detect": [
            "รหัสสินค้า 1234567890123",
            "invoice 3100600871635",
            # New post-Fix-2: no Thai-ID keyword, checksum invalid →
            # neither gate fires.
            "invoice 9999999999999",
        ],
    },
    {
        "label": "passport",
        "names": ("passport",),
        "should_detect": [
            "passport AA1234567",
            "หนังสือเดินทาง AB123456",
            # New post-Fix-3: Thai keyword variants.
            "พาสปอร์ต AA9876543",
            "เลขหนังสือเดินทาง: A12345678",
        ],
        "should_not_detect": [
            "รหัส AB12345",
            "product code AA123456",
        ],
    },
    {
        "label": "credential",
        # Either the secret-pattern detector or the bearer-token
        # detector firing is a valid hit.
        "names": ("api_key_secret", "bearer_token"),
        "should_detect": [
            "sk-ant-api03-abc123xyz",
            "api_key = 'ghp_xxxxxxxxxxxxxxxxxxxx'",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test",
            "OPENAI_API_KEY=sk-proj-abcdef123456",
            # New post-Fix-1 samples — current real-world prefixes
            # with 20+ char bodies that should now hit the
            # standalone (not assignment-gated) patterns.
            "key: sk-ant-api03-abc123xyz456def789ghi",
            "export ANTHROPIC_KEY=sk-ant-admin01-xyz789abc123def456ghi012",
            "OPENAI_API_KEY=sk-proj-abcdef123456ghijkl789012",
            "Authorization: sk-live-TestKey1234567890abcdef",
        ],
        "should_not_detect": [
            "สวัสดี key คือความสำเร็จ",
            "the key to success",
        ],
    },
    {
        "label": "thai_name",
        # ``thai_name`` covers the prefixed pattern (คุณ X);
        # ``thai_name_no_prefix`` only registers when NER is disabled;
        # ``presidio_ner`` covers bare names when NER is on.
        "names": ("thai_name", "thai_name_no_prefix", "presidio_ner"),
        "should_detect": [
            "ลูกค้าชื่อ สมชาย ใจดี โทรมา",
            "คุณ สมหญิง รักไทย ต้องการ",
        ],
        "should_not_detect": [
            "ภาษาไทย",
            "กรุงเทพมหานคร",
        ],
    },
    {
        "label": "ip_address",
        "names": ("ip_address",),
        "should_detect": [
            "server at 192.168.1.100",
            "IP: 10.0.0.1",
            "connect to 203.154.1.1",
        ],
        "should_not_detect": [
            "version 192.168",
            "ratio 10.0",
        ],
    },
]


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

# Module-level so xdist / parametrize share the registry.
_REGISTRY = get_default_registry()
# Cumulative results bucket — populated by the parametrized test below,
# read by the report finaliser fixture at session teardown.
RESULTS: list[dict] = []


def _measure(case: dict) -> dict:
    label = case["label"]
    names = set(case["names"])
    tp = fn = fp = 0
    fn_examples: list[str] = []
    fp_examples: list[str] = []

    for text in case["should_detect"]:
        matches = _REGISTRY.scan_text(text)
        if any(m.detector_name in names for m in matches):
            tp += 1
        else:
            fn += 1
            fn_examples.append(text)

    for text in case["should_not_detect"]:
        matches = _REGISTRY.scan_text(text)
        if any(m.detector_name in names for m in matches):
            fp += 1
            fp_examples.append(text)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return {
        "label": label,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "fn_examples": fn_examples,
        "fp_examples": fp_examples,
    }


@pytest.mark.parametrize("case", CASES, ids=[c["label"] for c in CASES])
def test_detector_accuracy(case):
    """Measure one detector category. Always passes — informational only.

    The function records each category's metrics into the module-level
    ``RESULTS`` list. ``print_report`` (autouse session fixture below)
    consumes that list and prints the consolidated table once when the
    session ends.
    """
    result = _measure(case)
    RESULTS.append(result)
    # Deliberately no asserts — the table is the output.


@pytest.fixture(scope="session", autouse=True)
def print_report(request):
    yield
    # Only print when this test file actually ran (RESULTS may be
    # empty on a -k filter that excludes test_detector_accuracy).
    if not RESULTS:
        return
    # Stable order regardless of pytest parallelisation.
    order = {c["label"]: i for i, c in enumerate(CASES)}
    RESULTS.sort(key=lambda r: order.get(r["label"], 99))

    # Force UTF-8 for the report block so the embedded Thai samples
    # in the FN/FP listings print cleanly on Windows consoles that
    # default to cp1252. We touch the underlying buffer once instead
    # of running every print() through ``.encode("utf-8")``.
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print()
    print("=" * 60)
    print("DLP ACCURACY REPORT")
    print("=" * 60)
    print(f"{'Detector':<18}{'Precision':>10}{'Recall':>9}{'TP':>4}{'FN':>4}{'FP':>4}")
    print("-" * 60)

    total_tp = total_fn = total_fp = 0
    for r in RESULTS:
        total_tp += r["tp"]
        total_fn += r["fn"]
        total_fp += r["fp"]
        marker = ""
        if r["fp"]:
            marker = f"  ← FP: {r['fp_examples'][0][:40]}"
        elif r["fn"]:
            marker = f"  ← FN: {r['fn_examples'][0][:40]}"
        print(
            f"{r['label']:<18}{r['precision']:>10.3f}{r['recall']:>9.3f}"
            f"{r['tp']:>4}{r['fn']:>4}{r['fp']:>4}{marker}"
        )

    overall_p = (
        total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 1.0
    )
    overall_r = (
        total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 1.0
    )
    print("-" * 60)
    print(
        f"{'OVERALL':<18}{overall_p:>10.3f}{overall_r:>9.3f}"
        f"{total_tp:>4}{total_fn:>4}{total_fp:>4}"
    )
    print("=" * 60)

    fns = [(r["label"], ex) for r in RESULTS for ex in r["fn_examples"]]
    fps = [(r["label"], ex) for r in RESULTS for ex in r["fp_examples"]]
    if fns:
        print("False Negatives (missed):")
        for label, ex in fns:
            print(f"  {label}: {ex!r}")
    if fps:
        print("False Positives (wrong detection):")
        for label, ex in fps:
            print(f"  {label}: {ex!r}")
    print("=" * 60)
