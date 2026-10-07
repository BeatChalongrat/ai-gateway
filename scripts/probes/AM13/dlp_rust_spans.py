"""Probe: what the Rust privacy engine (the one live runs) masks in sentences where a person goes on typing
after an address — asked over gRPC, the way the backend asks it. Run in a throwaway backend container on a
network that holds only the sidecar under test (scripts/probes/AM13/dlp_sidecar_check.sh)."""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")
from app.services.dlp import rust_dlp_client as rc  # noqa: E402

ADDRESS = "ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
CASES = [
    ("ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน " + ADDRESS + " ขอให้เรียงจากถูกสุด", ADDRESS),
    ("ขอซื้อเก้าอี้สำนักงาน 20 ตัว " + ADDRESS + " งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด", ADDRESS),
    ("ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ ช่วยตรวจว่าส่งได้ไหม", "ที่อยู่สำหรับจัดส่งสินค้า 99/1 หมู่ 2 ตำบลบางพลีใหญ่ สมุทรปราการ"),
    ("ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง ขอบคุณครับ", "ส่งที่ 12 ตำบลบางรัก อำเภอเมือง ลำปาง"),
    ("ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความ", "ที่อยู่ 55/12 ซอย 5 บางนา"),
    (ADDRESS, ADDRESS),
]


async def main() -> int:
    import os

    client = rc.RustDlpClient(os.environ.get("DLP_GRPC_ADDRESS", "dlp-sidecar:50051"), 5.0)
    bad = 0
    for text, want in CASES:
        out = await client.scan_text(text, language="th", ner_enabled=False)
        if out is None:
            print("MISS the sidecar gave no answer")
            bad += 1
            continue
        matches = getattr(out, "matches", out)
        spans = []
        for m in matches or []:
            det = getattr(m, "detector_name", None) or getattr(m, "detector", "")
            if "thai_address" in str(det):
                spans.append(text[m.start:m.end])
        ok = spans == [want]
        bad += 0 if ok else 1
        print("ok  " if ok else "MISS", f"{len(spans[0]) if spans else 0:3} chars masked | left: {text[len(text) - len(text.split(want, 1)[-1]):].strip() or '(nothing)'}" if ok else f"masked {spans!r}")
    print(f"{len(CASES) - bad}/{len(CASES)} as they should be")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
