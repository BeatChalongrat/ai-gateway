"""Probe: which lead-ins does the address detector hear? Asked of the Rust privacy engine over gRPC, the way
the backend asks it — every detector's findings, not only the address one, so "found by something else"
shows too. Two made-up addresses (an office with a floor and no house number; a full one with a house
number and a postcode), each after the ways a person writes where something is or goes. Prints, per
sentence, what was masked as an address and whether anything else covered the street.

    (throwaway backend container on a network with the sidecar)  python /probe/dlp_lead_matrix.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
from app.services.dlp import rust_dlp_client as rc  # noqa: E402

OFFICE = "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
FULL = "99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500"
LEADS = [
    "ส่งที่", "ที่อยู่ ", "อยู่ที่",                                  # found on 7–8 Oct
    "ส่งเอกสารไปที่", "จัดส่งไปที่", "ส่งของมาที่", "นัดรับที่",          # the owner's four
    "ส่งไปที่", "ส่งมาที่", "จัดส่งที่", "จัดส่งสินค้ามาที่", "ส่งถึง", "ส่งให้ที่",
    "นัดรับของที่", "มารับที่", "รับของที่", "รับสินค้าที่",
    "ไปที่", "มาที่", "นัดที่", "เจอกันที่", "พักที่", "ทำงานที่", "ออฟฟิศอยู่", "",
]


async def main() -> int:
    client = rc.RustDlpClient(os.environ.get("DLP_GRPC_ADDRESS", "dlp-sidecar:50051"), 5.0)
    print(f"{'lead-in':<20} | {'office, no house number':<32} | full address with house number")
    for lead in LEADS:
        cells = []
        for place in (OFFICE, FULL):
            text = f"ขอบคุณครับ {lead}{place} ภายในวันศุกร์นะครับ"
            out = await client.scan_text(text, language="th", ner_enabled=False)
            matches = list(getattr(out, "matches", out) or []) if out is not None else []
            addr = [text[m.start:m.end] for m in matches if "thai_address" in str(getattr(m, "detector_name", None) or getattr(m, "detector", ""))]
            street = text.index("ถนนสีลม")
            other = sorted({str(getattr(m, "detector_name", None) or getattr(m, "detector", "")) for m in matches
                            if m.start <= street < m.end and "thai_address" not in str(getattr(m, "detector_name", None) or getattr(m, "detector", ""))})
            if addr:
                tail_kept = "ภายในวันศุกร์" not in addr[0]
                cells.append(f"ADDRESS {len(addr[0])} chars" + ("" if tail_kept else " (ate the tail)"))
            elif other:
                cells.append("street covered by " + ",".join(other))
            else:
                cells.append("NOT MASKED")
        print(f"{(lead.strip() or '(nothing before it)'):<20} | {cells[0]:<32} | {cells[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
