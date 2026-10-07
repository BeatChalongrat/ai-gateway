"""Where does a Thai address END when a person goes on typing after it?

Found walking AM13 (2026-10-07): ``… ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด`` reached the
model as ``… <<ADDRESS_1>>`` — the request typed after the address was inside the mask. This prints, for a
handful of sentences of that shape, what the detector takes as the address and what it leaves.

    python -m evals.dlp.probe_address_tail            (from backend/, or inside the backend test lane)
"""

from __future__ import annotations

import sys

from app.services.dlp.detectors.thai_address import ThaiAddressDetector, trim_address_end, _ADDRESS_RE

CASES = [
    "ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด",
    "ขอซื้อเก้าอี้สำนักงาน 20 ตัว ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด",
    "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ",
    "ส่งที่ 99/1 ถนนสีลม กรุงเทพฯ ขอให้เรียงจากถูกสุด",
    "ส่งที่อาคารสาทรธานี ชั้น 12 ถนนสาทรเหนือ กรุงเทพฯ ภายในวันศุกร์ ขอใบเสนอราคาด้วย",
    "ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ",
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    det = ThaiAddressDetector()
    for text in CASES:
        found = det.detect(text)
        print(text)
        if not found:
            print("    (no address)")
        for m in found:
            print(f"    masked [{m.start}:{m.end}] {len(m.matched_text)} chars: {m.matched_text}")
            print(f"    left   : {text[m.end:].strip() or '(nothing)'}")
        for r in _ADDRESS_RE.finditer(text):
            end = trim_address_end(text, r.start(2), r.end(2))
            print(f"    body [{r.start(2)}:{r.end(2)}] trimmed to {end}{'  ← the whole body: the trim kept nothing and fell back' if end == r.end(2) else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
