"""AM13b — masking follows where the data goes, asked of the TEST stack's real gateway, both ways.

One sentence with an e-mail address, a phone number and a postal address in it (made up), one key (the test
stack's Agent Market runner), and per leg a header and a model. For each leg, three independent readings of
what happened to the sentence:

  * what the gateway SAYS it did                   — the response header X-Gateway-Masking
  * what the model SAW                             — it is asked to answer MASKED if the text it was given
                                                     holds a << placeholder and PLAIN if it does not
  * what the gateway WROTE DOWN                    — the request_logs row: dlp_input_action, the number of
                                                     findings, and whether the stored copy holds placeholders
                                                     (it must, both ways) or the address as typed (never)

    python scripts/probes/AM13/masking_probe.py closed:qwen3.8-27b-fp8 none:qwen3.8-27b-fp8 closed:deepseek-v4-flash

A leg is "<header>:<model>", header = closed (X-Sran-Egress: closed) or none. No key is printed.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
KEY = io.open(os.path.join(ROOT, "scripts/probes/AM1/results/runner_key.test"), encoding="utf-8").read().strip()
GATEWAY = os.environ.get("PROBE_GATEWAY", "http://localhost:18080")
PG = os.environ.get("PROBE_PG", "sran-test-postgres-1")

EMAIL = "somchai.probe@example.co.th"
SENTENCE = (f"ส่งใบกำกับภาษีให้คุณสมชาย อีเมล {EMAIL} โทร 081-234-5678 "
            "ที่อยู่ 99/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพฯ 10500")
SYSTEM = ("คุณเป็นผู้ตรวจข้อความ ตอบเพียงคำเดียวเป็นตัวพิมพ์ใหญ่ ไม่มีคำอื่น: "
          "ถ้าข้อความของผู้ใช้มีเครื่องหมายวงเล็บมุมสองชั้น (<<) อยู่ตรงไหนก็ตาม ให้ตอบ MASKED ถ้าไม่มีเลยให้ตอบ PLAIN")


def sql(q: str) -> str:
    r = subprocess.run(["docker", "exec", PG, "psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def leg(header: str, model: str) -> dict:
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json", "X-Sran-Fallback": "off"}
    if header == "closed":
        headers["X-Sran-Egress"] = "closed"
    body = json.dumps({"model": model, "stream": False, "max_tokens": 4000, "reasoning": {"mode": "off"},
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": SENTENCE}]}).encode("utf-8")
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body, headers=headers)
    out = {"leg": f"{header}:{model}"}
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            j = json.loads(r.read().decode("utf-8"))
            out["says"] = r.headers.get("X-Gateway-Masking") or "(no header)"
            out["provider"] = r.headers.get("X-Gateway-Provider") or j.get("provider")
            out["model_saw"] = " ".join((j["choices"][0]["message"].get("content") or "").split())[:40]
            out["reasons"] = [x for x in j.get("policy_reasons") or [] if x == "in_house_unmasked"]
            rid = j["id"]
    except urllib.error.HTTPError as e:
        out["error"] = f"{e.code} {e.read().decode('utf-8', 'replace')[:160]}"
        return out
    row = ""
    for _ in range(20):  # the row is written by a background worker
        row = sql("select dlp_input_action, dlp_input_findings, position('<<' in coalesce(input_masked,'')) > 0, "
                  f"position('{EMAIL}' in coalesce(input_masked,'') || coalesce(output_raw,'') || coalesce(output_final,'')) > 0, "
                  f"provider from request_logs where request_id = '{rid}'")
        if row:
            break
        time.sleep(0.5)
    if row:
        a, n, tokens, raw, provider = row.split("|")
        out["log"] = {"action": a, "findings": int(n), "stored_copy_has_placeholders": tokens == "t",
                      "stored_copy_has_the_address_as_typed": raw == "t", "provider": provider}
    else:
        out["log"] = "(no row)"
    return out


def main(argv: list[str]) -> int:
    legs = argv[1:] or ["closed:qwen3.8-27b-fp8", "none:qwen3.8-27b-fp8", "closed:deepseek-v4-flash"]
    results = []
    for spec in legs:
        header, model = spec.split(":", 1)
        r = leg(header, model)
        results.append(r)
        if "error" in r:
            print(f"{r['leg']:<34} ERROR {r['error']}")
            continue
        log = r["log"]
        if isinstance(log, dict):
            said = (f"log: {log['action']} · {log['findings']} findings · stored copy has placeholders: "
                    f"{log['stored_copy_has_placeholders']} · has the address as typed: {log['stored_copy_has_the_address_as_typed']}")
        else:
            said = "log: " + log
        print(f"{r['leg']:<34} gateway says masking {r['says']:<4} · the model saw: {r['model_saw']:<8} · {said}")
    name = os.environ.get("PROBE_OUT", "masking_probe.json")
    with io.open(os.path.join(ROOT, "scripts/probes/AM13/results", name), "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sentence": SENTENCE, "legs": results}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
