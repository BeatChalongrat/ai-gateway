"""Probe: every detector's own span on one sentence (Python engine), to see which one takes the tail of a line
after a street address. Run inside the backend image with the checkout's backend mounted read-only."""
import inspect
import sys

sys.stdout.reconfigure(encoding="utf-8")
from app.services.dlp.detectors.registry import get_default_registry  # noqa: E402

TEXTS = [
    "ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด",
    "<<<\nขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด\n>>>",
]
reg = get_default_registry()
print("scan_text signature:", inspect.signature(reg.scan_text))
for text in TEXTS:
    print(repr(text[:60]), "…", len(text), "chars")
    for det in reg.all():
        try:
            found = det.detect(text)
        except Exception as e:  # noqa: BLE001
            print("   ", det.name, "raised", type(e).__name__, e)
            continue
        for m in found:
            print(f"    {det.name:24} [{m.start}:{m.end}] {m.end - m.start:3} chars  {text[m.start:m.end]!r}")
    try:
        out = reg.scan_text(text)
        items = out if isinstance(out, list) else getattr(out, "matches", out)
        for m in items:
            print(f"    → scan_text: {getattr(m, 'detector_name', '?'):20} [{m.start}:{m.end}] {m.end - m.start} chars")
    except Exception as e:  # noqa: BLE001
        print("    scan_text raised", type(e).__name__, e)
