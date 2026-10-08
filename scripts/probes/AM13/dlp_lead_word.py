"""Probe: does the address detector need a lead word? The same office address after "ส่งที่" (found on
7 Oct 2026) and after other ways a person writes where something goes. Asked of the Python detector — the
twin the Rust engine is held to by the parity fixtures — inside a backend container; prints spans' lengths
and what was left, never more than these made-up sentences.

    docker exec -i sran-test-backend-1 python - < scripts/probes/AM13/dlp_lead_word.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
from app.services.dlp.detectors.registry import get_default_registry  # noqa: E402

PLACE = "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
CASES = [
    "ส่งที่" + PLACE,
    "ส่งเอกสารไปที่" + PLACE,
    "จัดส่งไปที่" + PLACE,
    "ส่งของมาที่" + PLACE,
    "นัดรับที่" + PLACE,
    "ที่อยู่ " + PLACE,
    "ออฟฟิศอยู่" + PLACE,
    "ส่งเอกสารไปที่ 99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500",
]
reg = get_default_registry()
for text in CASES:
    found = [m for m in reg.scan_text(text) if "address" in (getattr(m, "detector_name", "") or getattr(m, "detector", "") or "").lower()]
    spans = ", ".join(f"{(m.end - m.start)} chars" for m in found) or "NOT FOUND"
    print(f"{spans:<12} | {text}")
