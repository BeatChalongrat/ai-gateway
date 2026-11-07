"""FIX46 — run sentences through the thai_address detector on BOTH engines.

    docker exec ai-gateway-backend-1 python -m evals.dlp.probe_address [--json] [file.txt | -]

Reads one sentence per line (default: the built-in FIX46 cases) and prints,
per sentence, what Python's ``ThaiAddressDetector`` matched and what the
Rust sidecar (``RustDlpClient.scan_text``, ``thai_address`` findings only)
matched. Both engines, side by side, is the only way to say "parity" —
``make dlp-eval`` says it per category; this says it per sentence.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.services.dlp.detectors.thai_address import ThaiAddressDetector
from app.services.dlp.rust_dlp_client import RustDlpClient

CASES = [
    # #6 — building / room addresses and lead-in words
    "ไปที่ 23 ตึกบี ห้อง 402",
    "ที่อยู่ 23 ตึกบี ห้อง 402",
    "อยู่ที่ 23 ตึกบี ห้อง 402",
    "ส่งไปที่ 199/22 คอนโดลุมพินี ชั้น 8 ห้อง 812",
    # #5 — the three axis-A leaks
    "เขาอยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130",
    "ไปวัดหน้างานที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ",
    "ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000",
    "ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110",
    # must NOT fire
    "ไปที่หน้า 3 แล้วกดปุ่มสีเขียว",
    "ให้ไปที่ขั้นตอนที่ 2 ก่อน",
    "เลขที่ให้จำเมื่อกี้คืออะไร",
]


def py(text: str) -> list[str]:
    return [m.matched_text for m in ThaiAddressDetector().detect(text)]


async def rs(client: RustDlpClient, text: str) -> list[str] | str:
    res = await client.scan_text(text, language="th", ner_enabled=False)
    if res is None:
        return "RUST UNAVAILABLE"
    return [m.matched_text for m in res.matches if m.detector_name == "thai_address"]


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if args:
        src = sys.stdin if args[0] == "-" else open(args[0], encoding="utf-8")
        cases = [ln.rstrip("\n") for ln in src if ln.strip() and not ln.startswith("#")]
    else:
        cases = CASES
    client = RustDlpClient.instance()
    rows = []
    for t in cases:
        p = py(t)
        r = await rs(client, t)
        rows.append({"text": t, "python": p, "rust": r, "parity": p == r})
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for row in rows:
            flag = "OK " if row["parity"] else "DIFF"
            print(f"[{flag}] {row['text']}\n      py={row['python']}\n      rs={row['rust']}")
        print(f"\n{sum(1 for r in rows if r['parity'])}/{len(rows)} parity")
    return 0 if all(r["parity"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
