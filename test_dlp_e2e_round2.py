"""E2E DLP round-trip Round 2 — 10 complex multi-PII cases.

Each case carries 3-6 PII values mixed with trap items (product codes,
error messages, documentation examples). Same methodology as Round 1
(``test_dlp_e2e_50.py``): send via the gateway, read ``input_masked``
/ ``output_raw`` / ``output_final`` from request_logs, evaluate
mask + unmask + AI behaviour.

Run inside the backend container:
  docker compose exec backend python /app/test_dlp_e2e_round2.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, "/app")
from app.core.db import SessionLocal  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.request_log import RequestLogORM  # noqa: E402

import requests  # noqa: E402

KEY = os.environ.get("GW_API_KEY")
if not KEY:
    sys.exit("GW_API_KEY is not set — export a gateway API key before running this script")
URL = "http://localhost:8080/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json; charset=utf-8",
}

# Complex multi-PII cases. (id, prompt, list of expected-masked values).
ROUND2_CASES = [
    ("R01",
     "ช่วยสรุปข้อความแชทนี้ให้เป็นภาษาสุภาพและสั้นลง: ลูกค้าชื่อ Somchai Jaidee แจ้งว่าเข้าใช้งานระบบไม่ได้ตั้งแต่เมื่อวาน อีเมล somchai.j@example.com เบอร์ติดต่อ 081-234-5678 เคยยืนยันตัวตนด้วยเลขบัตรประชาชน 1101700203451 แต่ระบบขึ้นว่า verification failed และลูกค้าบอกว่าเคยชำระเงินผ่านบัตร 4111 1111 1111 1111 แล้ว ระบบยังไม่อัปเดตสถานะ ช่วยสรุปโดยไม่เปิดเผยข้อมูลสำคัญเกินจำเป็น",
     ["somchai.j@example.com", "081-234-5678", "1101700203451", "4111 1111 1111 1111"]),
    ("R02",
     "Please summarize this customer support chat professionally: John Doe contacted us using john.doe@company.com and +66 89 111 2222. He said his passport AA1234567 could not be verified during onboarding. He also pasted a password P@ssw0rd123! and said \"password is wrong\". The message also contains a sample text \"Authorization: Bearer <token>\" from API documentation. Please summarize what happened and mention what data should be removed before sharing internally.",
     ["john.doe@company.com", "+66 89 111 2222", "AA1234567", "P@ssw0rd123!"]),
    ("R03",
     "ช่วย rewrite ข้อความนี้ให้ปลอดภัยก่อนส่งต่อทีม support: ลูกค้า Wipa ส่งอีเมลมาจาก wipa.finance@abc.co.th แจ้งว่าขอคืนเงินเข้าบัญชี 123-4-56789-0 เพราะโอนผ่าน PromptPay 0899998888 แล้วระบบไม่ขึ้นยอด เธอยังแนบเลขผู้เสียภาษีบริษัท 0105558123456 และที่อยู่ 99/88 ถนนสุขุมวิท กรุงเทพ 10110 มาในแชทด้วย ช่วยทำให้ข้อความอ่านง่ายและไม่แสดงข้อมูลการเงินแบบเต็ม",
     ["wipa.finance@abc.co.th", "123-4-56789-0", "0899998888", "0105558123456"]),
    ("R04",
     "ช่วยแปลเป็นอังกฤษและสรุปความเสี่ยงของข้อความนี้: ทีม dev เผลอส่ง DB connection string postgres://admin:pass123@localhost:5432/app พร้อม API key sk-test-1234567890abcdef และ GitHub token ghp_1234567890abcdef1234567890abcdef มาในแชทเดียวกับข้อมูลลูกค้า Somchai email somchai@test.com phone 0812345678 ตอนท้ายยังมีคำว่า skincare product code is sk-test-cream-001 ซึ่งไม่ใช่ secret ช่วยตอบให้เหมาะกับ chat service",
     ["postgres://admin:pass123@localhost:5432/app", "sk-test-1234567890abcdef", "ghp_1234567890abcdef1234567890abcdef", "somchai@test.com", "0812345678"]),
    ("R05",
     "ช่วยสรุป case นี้แบบไม่เปิดเผยข้อมูลส่วนตัว: ลูกค้าแจ้งว่าเกิดวันที่ 12/05/1998 มีโรคเบาหวานและความดันสูง ต้องการเลื่อนนัด และให้เบอร์ติดต่อ 088-888-8888 พร้อมอีเมล patient.test@example.com ในข้อความเดียวกันมีเลข passport TH1234567 และเลขบัตรประชาชน 1101700203451 ด้วย ช่วยสรุปเฉพาะประเด็นที่ทีมบริการลูกค้าต้องรู้",
     ["12/05/1998", "088-888-8888", "patient.test@example.com", "TH1234567", "1101700203451"]),
    ("R06",
     "ช่วยทำข้อความนี้ให้เป็น customer note แบบ professional: ลูกค้า Michael Brown แจ้งว่า checkout failed โดยใช้ credit card 5555 5555 5555 4444 และ IBAN DE89370400440532013000 สำหรับการโอนเงินระหว่างประเทศ เขาติดต่อมาที่ michael.brown@example.com และบอกว่าเงินเดือน 85,000 บาทต่อเดือนใช้ประกอบการสมัครสินเชื่อ ช่วยสรุปโดยไม่แสดงเลขการเงินเต็ม",
     ["5555 5555 5555 4444", "DE89370400440532013000", "michael.brown@example.com"]),
    ("R07",
     "ช่วยตรวจว่าข้อความนี้ควรถูก mask อะไรบ้าง: \"ติดต่อคุณสมชายที่ 0812345678 หรือ somchai@test.com ได้เลย ส่วนรหัสสินค้า ABC-1234-TH ไม่ใช่เลขบัญชี และคำว่า password is wrong เป็นแค่ error message ไม่มีรหัสผ่านจริง แต่มี token จริงคือ Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx\"",
     ["0812345678", "somchai@test.com", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx"]),
    ("R08",
     "Please rewrite this chat in a safer way: Sarah Lee said her email sarah.lee@company.com is not receiving OTP. She gave phone number +66 81 222 3333, Thai ID 1101700203451, and bank account 987-6-54321-0 for refund verification. Later she pasted \"private key -----BEGIN PRIVATE KEY----- abcdef -----END PRIVATE KEY-----\" by mistake. Keep the useful support context but remove sensitive values.",
     ["sarah.lee@company.com", "+66 81 222 3333", "1101700203451", "987-6-54321-0", "-----BEGIN PRIVATE KEY----- abcdef -----END PRIVATE KEY-----"]),
    ("R09",
     "ช่วยสรุปข้อความยาวนี้ให้ทีมดูแบบปลอดภัย: ลูกค้าชื่อวิภาใช้ email wipa@test.com โทร 089-111-2222 แจ้งว่าระบบหักเงินซ้ำ 25,000 บาท จากบัตร 4111 1111 1111 1111 และขอคืนเงินเข้าบัญชี 123-4-56789-0 ระหว่างคุยลูกค้าส่งเลขบัตรประชาชน 1101700203451 มาเพื่อยืนยันตัวตน และพนักงานตอบกลับว่า \"กรุณาส่ง password มาให้ตรวจสอบ\" ช่วยสรุปพร้อมบอกว่าข้อความไหนไม่ควรส่งต่อ",
     ["wipa@test.com", "089-111-2222", "4111 1111 1111 1111", "123-4-56789-0", "1101700203451"]),
    ("R10",
     "ช่วย classify ข้อความนี้ว่าเสี่ยงระดับไหนและ rewrite ให้ปลอดภัย: user ส่งว่า API key คือ sk-live-abcdef1234567890, password คือ MyPass@123, email คือ user@example.com, phone คือ 080-123-4567, และมีประโยคว่า localhost:5432/app failed to connect ซึ่งเป็น error ธรรมดา ช่วยตอบแบบ chat service ไม่ต้องทำ action ใดๆ",
     ["sk-live-abcdef1234567890", "MyPass@123", "user@example.com", "080-123-4567"]),
]

# Trap items: (label, substring to search, case-id where it lives, should-be-masked-flag).
# ``should_mask=False`` means we want to verify the value still appears
# verbatim in input_masked (DLP did NOT mask it).
TRAP_ITEMS = [
    ("sk-test-cream-001 (product code)", "sk-test-cream-001", "R04", False),
    ("ABC-1234-TH (product code)", "ABC-1234-TH", "R07", False),
    ("'password is wrong' (error text)", "password is wrong", "R07", False),
    ("'Authorization: Bearer <token>' (doc example)", "Authorization: Bearer <token>", "R02", False),
    ("'localhost:5432/app' (error msg)", "localhost:5432/app", "R10", False),
    ("'85,000 บาท' (salary — known limitation)", "85,000", "R06", True),
]

REFUSAL_PATTERNS = (
    "can't assist",
    "cannot assist",
    "sorry, but i can",
    "i'm sorry, but",
    "i can't help",
    "i am unable to",
    "unable to assist",
    "ขออภัย แต่",
    "ไม่สามารถช่วย",
)

RN_TOKEN_RE = re.compile(r"<r\d+>")
LEGACY_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
PLACEHOLDER_OK = {"[REDACTED]", "[BLOCKED_BY_DLP]", "[MASKED_BY_DLP]"}


def fetch_log(request_id: str, timeout_s: float = 6.0) -> RequestLogORM | None:
    sess = SessionLocal()
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            row = sess.get(RequestLogORM, request_id)
            if row is not None:
                return row
            time.sleep(0.15)
    finally:
        sess.close()
    return None


def send_once(prompt: str, timeout_s: int = 60) -> dict:
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    t0 = time.time()
    try:
        r = requests.post(URL, headers=HEADERS, data=body, timeout=timeout_s)
        return {
            "http": r.status_code,
            "resp": r.json() if r.headers.get("content-type", "").startswith("application/json") else {},
            "latency": time.time() - t0,
            "error": None,
        }
    except requests.Timeout:
        return {"http": "TIMEOUT", "resp": {}, "latency": time.time() - t0, "error": "timeout"}
    except Exception as e:
        return {"http": "ERR", "resp": {}, "latency": time.time() - t0, "error": str(e)[:200]}


def find_token_for_value(input_raw: str | None, input_masked: str | None, value: str) -> str | None:
    """Best-effort lookup: which ``<rN>`` replaced ``value`` in input_masked.

    Walks the prefix of input_raw up to the value's first occurrence and
    extracts the masked-text segment that replaced it. Returns the first
    ``<rN>`` token in that segment, or ``None`` when we can't align
    the prefixes (e.g. preamble was prepended, value spans wrap, etc.).
    The result is informational only — the verdict columns rely on
    plain substring presence/absence.
    """
    if not input_raw or not input_masked or value not in input_raw:
        return None
    # input_masked usually has a defense preamble + "---\n" prefix.
    # Strip it so the alignment doesn't drift by the preamble length.
    body = input_masked
    sep = "\n---\n"
    if sep in body:
        body = body.split(sep, 1)[1]
    elif "---\n" in body:
        body = body.split("---\n", 1)[1]
    # Look around the value's index for any <rN> in the masked body.
    # Cheap heuristic: scan all <rN> in body and return the first one
    # whose surrounding 30 chars roughly match the words before/after
    # the value in input_raw.
    idx = input_raw.find(value)
    before = input_raw[max(0, idx - 15): idx]
    tokens = list(RN_TOKEN_RE.finditer(body))
    if not tokens:
        return None
    for m in tokens:
        seg_before = body[max(0, m.start() - 15): m.start()]
        # Loose match: at least 3 chars of `before` appear in `seg_before`.
        for window in (5, 4, 3):
            if len(before) >= window and before[-window:] in seg_before:
                return m.group()
    return tokens[0].group()  # best-guess


def evaluate(tid: str, prompt: str, expected: list[str]) -> dict:
    out = send_once(prompt)
    http = out["http"]
    resp = out["resp"]
    if http == "TIMEOUT":
        out = send_once(prompt, timeout_s=90)
        http = out["http"]
        resp = out["resp"]

    request_id = (resp.get("id") or "").strip()
    ai_text = ""
    try:
        ai_text = resp["choices"][0]["message"]["content"] or ""
    except Exception:
        pass
    ai_refused = any(p in ai_text.lower() for p in REFUSAL_PATTERNS)

    log = fetch_log(request_id) if request_id else None
    input_raw = log.input_raw if log else None
    input_masked = log.input_masked if log else None
    output_raw = log.output_raw if log else None
    output_final = log.output_final if log else None

    # Per-value verdict. For each expected value:
    #   mask_ok    = value absent from input_masked
    #   restore_ok = value present in output_final (only meaningful when
    #                output_raw carried an <rN> token to restore from)
    per_value: list[dict] = []
    mask_hits = 0
    restore_hits = 0
    for v in expected:
        masked = bool(input_masked and v not in input_masked)
        # Restore is only well-defined when the LLM actually wrote the
        # placeholder token back into its response — if it dropped the
        # token entirely, "value not in output_final" is OK (no leak).
        token_in_output_raw = bool(
            output_raw and RN_TOKEN_RE.search(output_raw)
        )
        restored = bool(output_final and v in output_final)
        # Token traceability (best-effort).
        token = find_token_for_value(input_raw, input_masked, v)
        per_value.append({
            "value": v,
            "masked": masked,
            "restored": restored,
            "token": token,
            "token_in_output_raw": token_in_output_raw,
        })
        if masked:
            mask_hits += 1
        if restored or not token_in_output_raw:
            # "Not echoed back" counts as a non-leak: the LLM
            # discarded the token, so there was nothing to restore.
            restore_hits += 1

    # Orphan tokens left in output_final (post-sanitizer should be 0
    # for fresh requests; cached responses got the same treatment).
    orphans: list[str] = []
    if output_final:
        for m in RN_TOKEN_RE.finditer(output_final):
            orphans.append(m.group())
        for m in LEGACY_TOKEN_RE.finditer(output_final):
            tok = m.group()
            if tok not in PLACEHOLDER_OK:
                orphans.append(tok)

    return {
        "id": tid,
        "prompt": prompt,
        "expected": expected,
        "http": http,
        "policy": resp.get("policy_action", "-"),
        "ai_refused": ai_refused,
        "ai_text": ai_text,
        "latency_s": round(out["latency"], 2),
        "error": out["error"],
        "input_raw": input_raw,
        "input_masked": input_masked,
        "output_raw": output_raw,
        "output_final": output_final,
        "per_value": per_value,
        "mask_hits": mask_hits,
        "restore_hits": restore_hits,
        "orphan_tokens": orphans,
    }


def print_case(r: dict) -> None:
    tid = r["id"]
    n = len(r["expected"])
    mask_icon = "✅" if r["mask_hits"] == n else "❌"
    restore_icon = "✅" if r["restore_hits"] == n else "❌"
    ai_icon = "✅" if (r["http"] == 200 and not r["ai_refused"]) else "❌"
    orphan_n = len(r["orphan_tokens"])
    print(
        f"{tid} | MASK: {mask_icon} {r['mask_hits']}/{n} | "
        f"UNMASK: {restore_icon} {r['restore_hits']}/{n} | "
        f"AI: {ai_icon} | "
        f"Orphan: {orphan_n} | "
        f"{r['latency_s']}s"
    )
    for pv in r["per_value"]:
        v_disp = pv["value"] if len(pv["value"]) <= 38 else pv["value"][:35] + "..."
        mk = "✅" if pv["masked"] else "❌"
        rs_icon = (
            "✅"
            if pv["restored"]
            else ("· (no echo)" if not pv["token_in_output_raw"] else "❌")
        )
        tok = pv["token"] or "<r?>"
        print(f"  {v_disp:<40s} → {tok:<8s} → {mk} mask, {rs_icon} restore")


def trap_report(results: list[dict]) -> None:
    print("\nTRAP ITEMS (should NOT be masked unless marked otherwise):")
    by_id = {r["id"]: r for r in results}
    for label, substr, case_id, should_mask in TRAP_ITEMS:
        r = by_id.get(case_id)
        if r is None or r["input_masked"] is None:
            print(f"  - {label:<55s} no log data")
            continue
        # Was it masked? "Yes" = substring is absent from input_masked.
        masked = substr not in r["input_masked"]
        expected = "YES" if should_mask else "NO"
        actual = "YES" if masked else "NO"
        ok = (masked == should_mask)
        flag = "✅" if ok else "⚠️"
        print(
            f"  - {label:<55s} masked? {actual:<3s} "
            f"(expected: {expected}) {flag}"
        )


def main() -> int:
    results: list[dict] = []
    for tid, prompt, expected in ROUND2_CASES:
        print(f"  -> {tid}", file=sys.stderr, flush=True)
        r = evaluate(tid, prompt, expected)
        results.append(r)
        print_case(r)
        time.sleep(2.0)

    with open("/app/test_round2_results.json", "w", encoding="utf-8") as f:
        json.dump(
            [{k: v for k, v in r.items() if k != "ai_text"} for r in results],
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ───── Summary ────────────────────────────────────────────────
    total = len(results)
    mask_correct = sum(
        1 for r in results if r["mask_hits"] == len(r["expected"])
    )
    unmask_correct = sum(
        1 for r in results if r["restore_hits"] == len(r["expected"])
    )
    ai_helpful = sum(
        1 for r in results if r["http"] == 200 and not r["ai_refused"]
    )
    total_orphans = sum(len(r["orphan_tokens"]) for r in results)
    # FNs: an expected value still present in input_masked.
    fn_rows = [
        r for r in results
        if r["mask_hits"] < len(r["expected"])
    ]
    fully_correct = sum(
        1 for r in results
        if r["mask_hits"] == len(r["expected"])
        and r["restore_hits"] == len(r["expected"])
        and r["http"] == 200
        and not r["ai_refused"]
        and not r["orphan_tokens"]
    )

    print("\n" + "═" * 60)
    print("ROUND 2 SUMMARY (complex multi-PII cases)")
    print("═" * 60)
    print(f"Total cases:      {total}")
    print(f"MASK correct:     {mask_correct}/{total}")
    print(f"UNMASK correct:   {unmask_correct}/{total}")
    print(f"AI helpful:       {ai_helpful}/{total}")
    print(f"Orphan <rN>:      {total_orphans}")
    print(f"Fully correct:    {fully_correct}/{total}")

    if fn_rows:
        print("\nFALSE NEGATIVES (per case, expected values still in input_masked):")
        for r in fn_rows:
            misses = [
                pv["value"] for pv in r["per_value"] if not pv["masked"]
            ]
            print(f"  - {r['id']}: missed {misses}")

    trap_report(results)

    r1 = 41
    r1_pct = (r1 / 50) * 100
    r2_pct = (fully_correct / total) * 100 if total else 0
    print("\nCOMPARISON:")
    print(f"  Round 1 (simple):  {r1}/50  ({r1_pct:.0f}%)")
    print(f"  Round 2 (complex): {fully_correct}/{total} ({r2_pct:.0f}%)")

    verdict = "YES" if fully_correct >= 7 else "PARTIAL" if fully_correct >= 4 else "NO"
    print(f"\nRound 2: {fully_correct}/{total}, DLP handles complex traffic: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
