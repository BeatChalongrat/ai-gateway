"""Live test: DLP system-prompt injection prevents LLM refusal.

Sends 3 test cases through the gateway and prints the 4-stage trace
to verify that the injected system prompt makes the LLM respond
normally to masked messages.

Run with the gateway running on port 8080:
    python test_dlp_prompt_live.py
"""

import json
import os
import sys
import time

import requests

BASE_URL = "http://127.0.0.1:28080"  # the gateway's plain loopback door (8080 is https since FIX133)
API_KEY = os.environ.get("AI_GATEWAY_API_KEY", "")
if not API_KEY:
    sys.exit("AI_GATEWAY_API_KEY env var is required (a gw_ key from /api-keys).")
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _safe(t: str) -> str:
    return t.encode("ascii", errors="replace").decode("ascii")


def _trunc(t: str, n: int = 100) -> str:
    return t[:n] + "..." if len(t) > n else t


# ---------------------------------------------------------------------------
# Admin session
# ---------------------------------------------------------------------------

def admin_login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/auth/session/login", json={"email": "admin", "password": "admin1234"})
    if r.status_code != 200:
        print(_safe(f"  [WARN] Admin login failed ({r.status_code})"))
    return s


def fetch_trace(sess: requests.Session, rid: str) -> dict | None:
    try:
        r = sess.get(f"{BASE_URL}/admin/requests/{rid}", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Test messages
# ---------------------------------------------------------------------------

MSG_TC6 = (
    "Employee report for Mr. John Smith, born 15/03/1990, SSN 123-45-6789. "
    "He is a practicing Muslim and attends the local mosque every Friday. "
    "He was diagnosed with depression last year and is currently on medication. "
    "Email: john.smith@company.com Phone: +1-555-012-3456"
)

MSG_TC1 = (
    "ผมชื่อนายสมศักดิ์ มั่งมี "
    "เลขบัตรประชาชน 3-1001-12345-67-4 "
    "อยู่บ้านเลขที่ 88/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพ 10500 "
    "ต้องการสมัครสินเชื่อส่วนบุคคล "
    "เบอร์โทรติดต่อ 089-999-1234 "
    "อีเมล somsak.m@gmail.com "
    "บัญชีธนาคารกสิกร 012-3-98765-4 "
    "พร้อมเพย์หมายเลข 0899991234 "
    "รายได้ต่อเดือน 85,000 บาท "
    "ช่วยวิเคราะห์ว่าผมควรกู้เท่าไหร่ดีครับ"
)

MSG_TC2 = (
    "สรุปข้อมูลพนักงานใหม่: "
    "นางสาวพิมพ์ลดา จันทร์สว่าง "
    "อายุ 28 ปี "
    "เกิด 22/08/1997 "
    "เลขบัตร 1-1007-54321-89-1 "
    "ผมนับถือศาสนาพุทธ "
    "ไปทำบุญที่วัดทุกวันพระ "
    "เคยมีประวัติคดีเล็กน้อย "
    "ศาลสั่งคุมประพฤติ 6 เดือนเมื่อปี 2562 "
    "ปัจจุบันเป็นโรคภูมิแพ้และรับยาแก้แพ้ต่อเนื่อง "
    "ใช้สแกนลายนิ้วมือเข้าออฟฟิศ "
    "เบอร์ 062-888-7777 "
    "ช่วยเขียน offer letter ให้หน่อย"
)

TESTS = [
    ("TC6: English Muslim (was refusing)", MSG_TC6),
    ("TC1: Thai Loan Application", MSG_TC1),
    ("TC2: HR Report / PDPA S26", MSG_TC2),
]

REFUSAL_PHRASES = [
    "I'm sorry", "I can't assist", "I cannot assist",
    "I'm unable", "I cannot help", "I can't help",
    "not able to", "cannot provide", "can't provide",
]

W = 80


def _box(ch: str = "="):
    return ch * W


def _stage(title: str, text: str | None):
    p = lambda s: print(_safe(s))
    p("")
    p(f"  {title}")
    p(f"  {'~' * (len(title) + 2)}")
    if not text:
        p("  (not available)")
        return
    for i, line in enumerate(text.split("\n")[:10]):
        p(f"  | {_trunc(line, W - 6)}")
    if text.count("\n") > 9:
        p(f"  | ... ({text.count(chr(10)) + 1} lines)")


def main():
    p = lambda s: print(_safe(s))

    p(f"\n{'=' * W}")
    p(f"  DLP SYSTEM PROMPT INJECTION - LIVE TEST")
    p(f"  Verifying LLM no longer refuses masked messages")
    p(f"{'=' * W}")

    # Health check
    try:
        requests.get(f"{BASE_URL}/health", timeout=5).json()
    except Exception as e:
        p(f"\n  [ERROR] Gateway not reachable: {e}")
        sys.exit(1)

    admin = admin_login()
    results = []

    for label, message in TESTS:
        p(f"\n{'=' * W}")
        p(f"  {label}")
        p(f"{'=' * W}")

        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": message}],
            "max_tokens": 300,
        }
        t0 = time.time()
        resp = requests.post(f"{BASE_URL}/v1/chat/completions", headers=HEADERS, json=payload, timeout=60)
        elapsed = int((time.time() - t0) * 1000)

        p(f"  Status: {resp.status_code} | {elapsed}ms")

        body = resp.json()
        reply = ""
        request_id = ""

        if resp.status_code == 200:
            request_id = body.get("id", "")
            action = body.get("policy_action", "?")
            reply = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            p(f"  Action: {action} | Request: {request_id[:20]}...")
        elif resp.status_code == 403:
            action = "block"
            p(f"  BLOCKED: {body.get('error', {}).get('code', '?')}")

        # Check for refusal
        is_refusal = any(phrase.lower() in reply.lower() for phrase in REFUSAL_PHRASES)

        # Fetch trace
        trace = None
        if request_id:
            time.sleep(0.5)
            trace = fetch_trace(admin, request_id)

        # Print 4-stage trace
        if trace:
            _stage("USER -> SERVICE (original)", trace.get("input_raw"))
            _stage("SERVICE -> AI (masked + system prompt)", trace.get("input_masked"))
            _stage("AI -> SERVICE (LLM response)", trace.get("output_raw"))
            _stage("SERVICE -> USER (restored)", trace.get("output_final"))

            # Check system prompt injection
            input_masked = trace.get("input_masked", "")
            has_dlp_prompt = "privacy placeholders" in input_masked
            p(f"\n  System prompt injected: {'YES' if has_dlp_prompt else 'NO'}")
        else:
            _stage("USER -> SERVICE (original)", message)
            _stage("SERVICE -> USER (response)", reply)

        # Verdict
        p(f"  LLM refused: {'YES -- FAIL' if is_refusal else 'NO -- OK'}")
        if is_refusal:
            p(f"  Refusal text: {_trunc(reply, 80)}")

        verdict = "PASS" if (not is_refusal and resp.status_code == 200) else "FAIL"
        p(f"  Result: {verdict}")
        results.append((label, resp.status_code, is_refusal, verdict))
        time.sleep(1)

    # Summary
    p(f"\n{'=' * W}")
    p(f"  SUMMARY")
    p(f"{'=' * W}")
    p(f"  {'Test':<45} {'Status':<8} {'Refused':<10} {'Result'}")
    p(f"  {'-'*45} {'-'*8} {'-'*10} {'-'*8}")
    all_pass = True
    for label, status, refused, verdict in results:
        p(f"  {label:<45} {status:<8} {'YES' if refused else 'NO':<10} {verdict}")
        if verdict != "PASS":
            all_pass = False

    p(f"\n  {'All tests passed!' if all_pass else 'Some tests failed.'}")
    p(f"{'=' * W}\n")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
