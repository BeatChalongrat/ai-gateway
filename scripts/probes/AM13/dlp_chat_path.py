"""Probe: what of an address actually reaches a model on the masked path — the whole gateway, every detector
and the name recognition the tenant runs with, not one engine asked alone.

Each made-up sentence is sent to the IN-HOUSE model with no egress header (so it is masked as any chat
is), and the answer is read from the gateway's own row: how many findings, by which detectors, and whether
the street and the house number are still readable in ``input_masked`` — the text the model was handed.
Nothing leaves the organisation; the sentences are the only text sent.

    PROBE_TARGET=live python scripts/probes/AM13/dlp_chat_path.py            # live's gateway, live's runner key
    python scripts/probes/AM13/dlp_chat_path.py                               # the test stack
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masking_probe as mp  # noqa: E402 — the key (never printed), the gateway and the database of the target

OFFICE = "สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ"
FULL = "99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500"
CASES = [
    ("ส่งที่", OFFICE), ("ส่งเอกสารไปที่", OFFICE), ("จัดส่งไปที่", OFFICE), ("ส่งของมาที่", OFFICE), ("นัดรับที่", OFFICE),
    ("ส่งที่", FULL), ("นัดรับที่", FULL), ("มารับที่", FULL), ("ส่งถึง", FULL), ("เจอกันที่", FULL), ("", FULL),
]
if len(sys.argv) > 1:
    CASES = [tuple(a.split("|", 1)) if "|" in a else ("", a) for a in sys.argv[1:]]
    CASES = [(lead, {"OFFICE": OFFICE, "FULL": FULL}.get(place, place)) for lead, place in CASES]


def ask(text: str) -> dict:
    body = json.dumps({"model": "qwen3.8-27b-fp8", "stream": False, "max_tokens": 600, "reasoning": {"mode": "off"},
                       "messages": [{"role": "system", "content": "ตอบคำเดียวว่า รับทราบ"}, {"role": "user", "content": text}]}).encode("utf-8")
    req = urllib.request.Request(mp.GATEWAY + "/v1/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + mp.KEY, "Content-Type": "application/json", "X-Sran-Fallback": "off"})
    try:
        with urllib.request.urlopen(req, timeout=240, context=mp.SSL) as r:
            rid = json.loads(r.read().decode("utf-8"))["id"]
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code} {e.read().decode('utf-8', 'replace')[:120]}"}
    row = ""
    for _ in range(24):
        row = mp.sql("select dlp_input_findings || '|' || (position('ถนนสีลม' in coalesce(input_masked,'')) > 0)::text || '|' || "
                     "(position('99/9' in coalesce(input_masked,'')) > 0)::text || '|' || coalesce((select string_agg(distinct f->>'detector', ',') "
                     "from jsonb_array_elements(dlp_findings_json::jsonb) f where f->>'direction' = 'input'), '') || '|' || "
                     "coalesce(substring(input_masked from 'ขอบคุณครับ.*ภายใน[^ ]* *[^ ]*'), '') "
                     f"from request_logs where request_id = '{rid}'")
        if row:
            break
        time.sleep(0.5)
    if not row:
        return {"error": "no row"}
    n, street, house, detectors, handed = row.split("|", 4)
    return {"findings": int(n), "street_readable": street == "true", "house_number_readable": house == "true", "detectors": detectors,
            "handed": handed}


def main() -> int:
    out = []
    print(f"{'lead-in':<18} | {'address':<24} | what the model was handed")
    for lead, place in CASES:
        text = f"ขอบคุณครับ {lead}{place} ภายในวันศุกร์นะครับ"
        r = ask(text)
        kind = "office, no house no." if place == OFFICE else "full, with house no." if place == FULL else place[:22]
        if "error" in r:
            said = "ERROR " + r["error"]
        elif r["street_readable"]:
            said = f"THE STREET AS TYPED{' and the house number' if r['house_number_readable'] and '99/9' in place else ''} · {r['findings']} finding(s) [{r['detectors']}]"
        else:
            said = f"masked · {r['findings']} finding(s) [{r['detectors']}]"
        print(f"{(lead or '(nothing)'):<18} | {kind:<24} | {said}")
        if r.get("handed"):
            print(f"{'':<18} |   handed to the model: {r['handed']}")
        out.append({"lead": lead, "place": place, **r})
        time.sleep(2.5)  # the key's own rate limit
    name = os.environ.get("PROBE_OUT", "dlp_chat_path.json")
    with io.open(os.path.join(mp.ROOT, "scripts/probes/AM13/results", name), "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "target": "live" if mp.LIVE else "test", "cases": out}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
