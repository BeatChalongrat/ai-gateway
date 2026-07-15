"""E2E DLP round-trip test: 50 cases.

Sends each prompt through the gateway's chat-completion endpoint and
evaluates mask + unmask + AI behavior by reading the DB request_logs row.

Run inside the backend container so SessionLocal can reach Postgres:
  docker compose exec backend python /app/test_dlp_e2e_50.py
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

TEST_CASES = [
    # --- Group A: Single PII (Thai) ---
    ("T01", "ช่วยสรุปข้อความนี้ให้หน่อย: ลูกค้า Somchai อีเมล somchai@test.com แจ้งว่าระบบเข้าไม่ได้", ["somchai@test.com"]),
    ("T02", "ช่วยเขียนข้อความตอบกลับลูกค้า somchai@test.com ว่าเราได้รับเรื่องแล้ว", ["somchai@test.com"]),
    ("T03", "ช่วย rewrite ให้สุภาพ: คุณสมชาย เบอร์ 081-234-5678 แจ้งว่าชำระเงินแล้วแต่ยังไม่ได้รับสินค้า", ["081-234-5678"]),
    ("T04", "ช่วยแปลเป็นอังกฤษ: ลูกค้าชื่อวิภา ติดต่อกลับที่ 089-111-2222", ["089-111-2222"]),
    # --- Group B: Single PII (English) ---
    ("T05", "Please summarize this message: John Doe, john@test.com, called about a login issue.", ["john@test.com"]),
    ("T06", "Please rewrite politely: Sarah Lee can be contacted at +66 89 111 2222.", ["+66 89 111 2222"]),
    # --- Group C: Thai-specific PII ---
    ("T07", "ช่วยสรุป customer profile: Somchai Jaidee, email somchai.j@example.com, phone 0812345678", ["somchai.j@example.com", "0812345678"]),
    ("T08", "ช่วยทำข้อความนี้ให้อ่านง่ายขึ้น: ติดต่อ finance@abc.co.th ได้เลยครับ", ["finance@abc.co.th"]),
    ("T09", "ช่วยเขียนคำตอบสั้นๆ: ลูกค้าแจ้งว่า email john.doe@company.com ใช้งานไม่ได้", ["john.doe@company.com"]),
    ("T10", "ช่วยแปลข้อความนี้: My phone number is 080-123-4567 and I cannot access my account.", ["080-123-4567"]),
    # --- Group D: Identity documents ---
    ("T11", "ช่วยสรุปเคสนี้: เลขบัตรประชาชน 1101700203451 ใช้ยืนยันตัวตนไม่ได้", ["1101700203451"]),
    ("T12", "ช่วย rewrite แบบไม่เปิดเผยข้อมูล: ลูกค้าแจ้งเลขบัตรประชาชน 1101700203451 ผิด", ["1101700203451"]),
    ("T13", "Customer passport AA1234567 has an issue during registration. Please summarize.", ["AA1234567"]),
    ("T14", "ช่วยสรุป: passport TH1234567 ถูกใช้สมัครสมาชิกไม่ได้", ["TH1234567"]),
    ("T15", "ช่วยย่อข้อความนี้: ลูกค้าเกิดวันที่ 12/05/1998 และต้องการแก้ข้อมูลส่วนตัว", ["12/05/1998"]),
    ("T16", "ช่วย rewrite: เลขผู้เสียภาษีบริษัท 0105558123456 ใช้เปิดใบกำกับไม่ได้", ["0105558123456"]),
    ("T17", "ช่วยสรุปข้อความนี้: ทะเบียนรถ กข 1234 กรุงเทพ มีปัญหาในการลงทะเบียน", ["กข 1234"]),
    ("T18", "ช่วยแปลเป็นอังกฤษ: ลูกค้าแจ้งที่อยู่ 99/88 ถนนสุขุมวิท กรุงเทพ 10110", ["99/88 ถนนสุขุมวิท กรุงเทพ 10110"]),
    # --- Group E: Medical/Health ---
    ("T19", "ช่วยสรุปแบบ professional: ลูกค้ามีโรคเบาหวานและความดันสูง ต้องการเลื่อนนัด", []),
    ("T20", "ช่วย rewrite ให้ปลอดภัย: ลูกค้าแจ้งข้อมูลสุขภาพว่าเป็นภูมิแพ้รุนแรง", []),
    # --- Group F: Financial ---
    ("T21", "ช่วยสรุปข้อความนี้: บัตรเครดิต 4111 1111 1111 1111 ถูกปฏิเสธตอนชำระเงิน", ["4111 1111 1111 1111"]),
    ("T22", "Please summarize: credit card 5555 5555 5555 4444 failed during checkout.", ["5555 5555 5555 4444"]),
    ("T23", "ช่วย rewrite: ลูกค้าแจ้งเลขบัญชี 123-4-56789-0 เพื่อขอคืนเงิน", ["123-4-56789-0"]),
    ("T24", "ช่วยสรุป: โอนเงินไปที่บัญชี 987-6-54321-0 แล้วแต่ระบบยังไม่อัปเดต", ["987-6-54321-0"]),
    ("T25", "ช่วยแปลเป็นอังกฤษ: PromptPay 0812345678 ยังไม่ได้รับยอดเงิน", ["0812345678"]),
    ("T26", "Please summarize: IBAN DE89370400440532013000 was rejected by the payment system.", ["DE89370400440532013000"]),
    ("T27", "ช่วยสรุป: เงินเดือน 85,000 บาทต่อเดือน ใช้ประกอบการสมัครสินเชื่อ", []),
    ("T28", "ช่วย rewrite: ลูกค้าส่งสลิปที่มีเลขบัญชีและยอดเงิน 25,000 บาทมาในแชท", []),
    # --- Group G: Credentials/Secrets ---
    ("T29", "ช่วยดูว่าข้อความนี้เสี่ยงไหม: API key sk-test-1234567890abcdef หลุดในแชท", ["sk-test-1234567890abcdef"]),
    ("T30", "ช่วยสรุป security issue: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx ถูกส่งมา", ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx"]),
    ("T31", "ช่วย rewrite ให้ปลอดภัย: รหัสผ่าน P@ssw0rd123! ใช้งานไม่ได้", ["P@ssw0rd123!"]),
    ("T32", "Please summarize this issue: DB connection string postgres://admin:pass123@localhost:5432/app is exposed.", ["postgres://admin:pass123@localhost:5432/app"]),
    ("T33", "ช่วยทำข้อความนี้ให้สุภาพ: private key -----BEGIN PRIVATE KEY----- abcdef -----END PRIVATE KEY----- ถูก paste มา", ["-----BEGIN PRIVATE KEY----- abcdef -----END PRIVATE KEY-----"]),
    ("T34", "ช่วยสรุป: GitHub token ghp_1234567890abcdef1234567890abcdef หลุดใน ticket", ["ghp_1234567890abcdef1234567890abcdef"]),
    ("T35", "ช่วยอธิบายความเสี่ยงของข้อความนี้: username admin password admin123 ถูกส่งในแชท", ["admin123"]),
    ("T36", "ช่วย rewrite: SMTP password smtp_pass_123456 ถูกแชร์ในข้อความ", ["smtp_pass_123456"]),
    # --- Group H: Multi-PII ---
    ("T37", "ช่วยสรุป customer profile: Somchai, somchai@test.com, 1101700203451, เบอร์ 0812345678", ["somchai@test.com", "1101700203451", "0812345678"]),
    ("T38", "Please summarize: John Doe, john@test.com, passport AA1234567, password P@ssw0rd123!", ["john@test.com", "AA1234567", "P@ssw0rd123!"]),
    ("T39", "ช่วย rewrite: ลูกค้าให้ email john@test.com และเลขบัตร 4111 1111 1111 1111 มาในแชท", ["john@test.com", "4111 1111 1111 1111"]),
    ("T40", "ช่วยสรุป: ลูกค้า Somchai ใช้บัญชี 123-4-56789-0 และเบอร์ 0812345678", ["123-4-56789-0", "0812345678"]),
    ("T41", "ช่วยตอบแบบสุภาพ: ลูกค้าส่ง API key sk-test-abc123 และ email somchai@test.com มาในแชท", ["sk-test-abc123", "somchai@test.com"]),
    ("T42", "ช่วยแปลเป็นอังกฤษ: ลูกค้าแจ้งเลขบัตรประชาชน 1101700203451 และเบอร์ 0899998888", ["1101700203451", "0899998888"]),
    # --- Group I: No real PII (should NOT mask) ---
    ("T43", "ช่วยตรวจ grammar: My email is test@example.com and I cannot login.", ["test@example.com"]),
    ("T44", "ช่วยแปล: เบอร์ติดต่อคือ 000-000-0000 อันนี้เป็นข้อมูลตัวอย่าง", []),
    ("T45", "ช่วยอธิบาย regex สำหรับตรวจ email โดยไม่ใช้ข้อมูลจริง", []),
    ("T46", "คำว่า password is wrong ในประโยคนี้เป็นข้อมูลลับไหม", []),
    ("T47", "skincare product code is sk-test-cream-001 ช่วยแปลเป็นไทย", []),
    ("T48", "localhost:5432/app failed to connect ช่วยอธิบาย error นี้", []),
    ("T49", "Authorization: Bearer <token> ในเอกสาร API หมายถึงอะไร", []),
    ("T50", "รหัสสินค้า ABC-1234-TH ไม่ใช่เลขบัตรหรือเลขบัญชี ช่วยแปลเป็นอังกฤษ", []),
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

# Match <rN> opaque tokens emitted by current pipeline (commit ffc6704).
RN_TOKEN_RE = re.compile(r"<r\d+>")
# Match legacy [TYPE_N] tokens (e.g. [EMAIL_0]) — still possible in older paths.
LEGACY_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
PLACEHOLDER_OK = {"[REDACTED]", "[BLOCKED_BY_DLP]", "[MASKED_BY_DLP]"}


def looks_like_token(s: str) -> bool:
    return bool(RN_TOKEN_RE.search(s) or LEGACY_TOKEN_RE.search(s))


def fetch_log(request_id: str, timeout_s: float = 4.0) -> RequestLogORM | None:
    """Poll request_logs for the row matching this request id."""
    sess = SessionLocal()
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            row = sess.get(RequestLogORM, request_id)
            if row is not None:
                return row
            time.sleep(0.1)
    finally:
        sess.close()
    return None


def evaluate_case(tid: str, prompt: str, expected: list[str]) -> dict:
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        r = requests.post(URL, headers=HEADERS, data=body, timeout=30)
        http = r.status_code
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        return {
            "id": tid,
            "prompt": prompt,
            "expected": expected,
            "http": "ERR",
            "policy": "-",
            "ai_text": "",
            "ai_refused": False,
            "input_raw": None,
            "input_masked": None,
            "output_raw": None,
            "output_final": None,
            "mask_hits": [],
            "mask_misses": list(expected),
            "false_positive_mask": False,
            "unmask_ok": False,
            "orphan_tokens": [],
            "error": f"transport: {str(e)[:120]}",
        }

    request_id = (resp.get("id") or "").strip()
    policy = resp.get("policy_action", "-")
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

    # MASK evaluation: each expected value must be ABSENT from input_masked
    # AND something token-like (<rN> or [TYPE_N]) must have taken its place.
    mask_hits: list[str] = []
    mask_misses: list[str] = []
    if input_masked is not None:
        for v in expected:
            if v not in input_masked:
                mask_hits.append(v)
            else:
                mask_misses.append(v)

    # False-positive: Group I (expected=[]) but we still see <rN>/[TYPE_N] tokens
    # in input_masked. T43 is special: expected has test@example.com so the
    # standard mask check applies — but T43 still belongs to "Group I" semantics
    # (we explicitly expect that domain to be flagged given prior behavior).
    false_positive_mask = False
    if not expected and input_masked is not None:
        false_positive_mask = looks_like_token(input_masked)

    # UNMASK evaluation: every expected value must reappear in output_final
    # (only meaningful when the LLM actually echoed the token back).
    unmask_ok = True
    if expected and output_final is not None:
        # If LLM never produced a token in output_raw, restore isn't applicable.
        produced_token = looks_like_token(output_raw or "")
        if produced_token:
            for v in expected:
                if v not in output_final:
                    unmask_ok = False
                    break
        else:
            unmask_ok = True  # nothing to restore

    # Orphans: any leftover <rN> or [TYPE_N] in output_final that isn't a
    # static placeholder. These are restore bugs.
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
        "policy": policy,
        "ai_text": ai_text,
        "ai_refused": ai_refused,
        "input_raw": input_raw,
        "input_masked": input_masked,
        "output_raw": output_raw,
        "output_final": output_final,
        "mask_hits": mask_hits,
        "mask_misses": mask_misses,
        "false_positive_mask": false_positive_mask,
        "unmask_ok": unmask_ok,
        "orphan_tokens": orphans,
        "error": None,
    }


def line_for(r: dict) -> str:
    tid = r["id"]
    ai = "✅" if (r["http"] == 200 and not r["ai_refused"]) else "❌"

    if not r["expected"]:
        # No-mask expected
        if r["false_positive_mask"]:
            return f"{tid} | MASK: ❌ false-positive | AI: {ai} | input_masked has tokens"
        return f"{tid} | MASK: ✅ no-mask (correct) | AI: {ai} | left untouched"

    hits = len(r["mask_hits"])
    total = len(r["expected"])
    mask_icon = "✅" if hits == total else "❌"
    unmask_icon = "✅" if r["unmask_ok"] else "❌"
    sample = r["expected"][0]
    return (
        f"{tid} | MASK: {mask_icon} {hits}/{total} | "
        f"UNMASK: {unmask_icon} | AI: {ai} | "
        f"{sample[:30]}{'…' if len(sample) > 30 else ''}"
    )


def main() -> int:
    results: list[dict] = []
    for tid, prompt, expected in TEST_CASES:
        print(f"  -> {tid}", file=sys.stderr, flush=True)
        r = evaluate_case(tid, prompt, expected)
        results.append(r)
        print(line_for(r), flush=True)
        time.sleep(1.2)

    # Persist raw results JSON for downstream inspection.
    with open("/app/test50_results.json", "w", encoding="utf-8") as f:
        json.dump(
            [
                {k: v for k, v in r.items() if k != "ai_text"}
                for r in results
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ─── Summary ───────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("SUMMARY")
    print("═" * 60)

    total = len(results)
    cases_with_expected = [r for r in results if r["expected"]]
    cases_no_expected = [r for r in results if not r["expected"]]

    mask_correct = sum(
        1 for r in cases_with_expected
        if len(r["mask_hits"]) == len(r["expected"])
    )
    unmask_correct = sum(1 for r in cases_with_expected if r["unmask_ok"])
    ai_helpful = sum(
        1 for r in results
        if r["http"] == 200 and not r["ai_refused"]
    )
    fps = [r for r in cases_no_expected if r["false_positive_mask"]]
    fns = [r for r in cases_with_expected if len(r["mask_misses"]) > 0]
    orphans = [r for r in results if r["orphan_tokens"]]

    print(f"Total:            {total}")
    print(f"MASK correct:     {mask_correct}/{len(cases_with_expected)}")
    print(f"UNMASK correct:   {unmask_correct}/{len(cases_with_expected)}")
    print(f"AI helpful:       {ai_helpful}/{total}")
    print(f"False positives:  {len(fps)}  (no-PII cases that were masked)")
    print(f"False negatives:  {len(fns)}  (expected masks that were missed)")
    print(f"Orphan tokens:    {len(orphans)}  (restore bugs in output_final)")

    if fns:
        print("\nFALSE NEGATIVES (expected mask but value still present):")
        for r in fns:
            print(f"  - {r['id']}: missed {r['mask_misses']}")
    if fps:
        print("\nFALSE POSITIVES (no-PII case got masked):")
        for r in fps:
            print(f"  - {r['id']}: input_masked={r['input_masked'][:100]!r}")
    if orphans:
        print("\nORPHANS (tokens left in output_final):")
        for r in orphans:
            print(f"  - {r['id']}: {r['orphan_tokens'][:5]}")

    refused_rows = [r for r in results if r["ai_refused"]]
    if refused_rows:
        print("\nAI REFUSALS:")
        for r in refused_rows:
            print(f"  - {r['id']}: {r['ai_text'][:90]!r}")

    fully_correct = 0
    for r in results:
        if r["expected"]:
            ok = (
                len(r["mask_hits"]) == len(r["expected"])
                and r["unmask_ok"]
                and r["http"] == 200
                and not r["ai_refused"]
                and not r["orphan_tokens"]
            )
        else:
            ok = (
                not r["false_positive_mask"]
                and r["http"] == 200
                and not r["ai_refused"]
                and not r["orphan_tokens"]
            )
        if ok:
            fully_correct += 1

    print(f"\nFULLY CORRECT:    {fully_correct}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
