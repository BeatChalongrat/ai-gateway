"""Live DLP Gateway test with full 4-stage trace.

Sends real HTTP requests to POST /v1/chat/completions, then fetches the
admin trace (input_raw → input_masked → output_raw → output_final) via
GET /admin/requests/{id}.

Run with the gateway running on port 8080:
    python test_live_gateway_dlp.py

Requires:
    - Gateway server running (uvicorn app.main:app --port 8080)
    - Active API key in the database
    - OpenAI provider key configured (for gpt-4o-mini)
    - Admin user credentials (admin / admin1234)
"""

import json
import os
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "http://127.0.0.1:8081"
GATEWAY_URL = f"{BASE_URL}/v1/chat/completions"
ADMIN_LOGIN_URL = f"{BASE_URL}/auth/session/login"
ADMIN_REQUESTS_URL = f"{BASE_URL}/admin/requests"
API_KEY = os.environ.get("AI_GATEWAY_API_KEY", "")
if not API_KEY:
    sys.exit("AI_GATEWAY_API_KEY env var is required (a gw_ key from /api-keys).")
ADMIN_USER = os.environ.get("AI_GATEWAY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("AI_GATEWAY_ADMIN_PASS", "admin1234")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def _safe(text: str) -> str:
    """Replace non-ASCII chars with ? for Windows cp1252 console."""
    return text.encode("ascii", errors="replace").decode("ascii")


def _trunc(text: str, maxlen: int = 120) -> str:
    if len(text) <= maxlen:
        return text
    return text[:maxlen] + "..."


# ---------------------------------------------------------------------------
# Test messages
# ---------------------------------------------------------------------------

MSG1_LOAN = (
    "ผมชื่อนายสมศักดิ์"
    " มั่งมี "
    "เลขบัตรประชาชน 3-1001-12345-67-4 "
    "อยู่บ้านเลขที่ 88/9 "
    "ถนนสีลม "
    "แขวงสีลม "
    "เขตบางรัก "
    "กรุงเทพ 10500 "
    "ต้องการสมัครสินเชื่อ"
    "ส่วนบุคคล "
    "เบอร์โทรติดต่อ 089-999-1234 "
    "อีเมล somsak.m@gmail.com "
    "บัญชีธนาคารกสิกร 012-3-98765-4 "
    "พร้อมเพย์หมายเลข 0899991234 "
    "รายได้ต่อเดือน 85,000 บาท "
    "ช่วยวิเคราะห์ว่า"
    "ผมควรกู้เท่าไหร่ดีครับ"
)

MSG2_HR = (
    "สรุปข้อมูลพนักงานใหม่: "
    "นางสาวพิมพ์ลดา "
    "จันทร์สว่าง "
    "อายุ 28 ปี "
    "เกิด 22/08/1997 "
    "เลขบัตร 1-1007-54321-89-1 "
    "ผมนับถือศาสนาพุทธ "
    "ไปทำบุญที่วัดทุกวันพระ "
    "เคยมีประวัติคดีเล็กน้อย "
    "ศาลสั่งคุมประพฤติ 6 เดือน"
    "เมื่อปี 2562 "
    "ปัจจุบันเป็นโรคภูมิแพ้"
    "และรับยาแก้แพ้ต่อเนื่อง "
    "ใช้สแกนลายนิ้วมือเข้าออฟฟิศ "
    "เบอร์ 062-888-7777 "
    "ช่วยเขียน offer letter ให้หน่อย"
)

MSG3_CREDS = (
    "ช่วยดีบัก connection ให้หน่อย "
    "มัน timeout ตลอด\n"
    "DATABASE_URL=postgresql://sran_admin:P@ssw0rd!@db.prod.sran.io:5432/sran_production\n"
    "ANTHROPIC_API_KEY=sk-ant-api03-real-key-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLM\n"
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA7CpW+GmYJh3GnGdkfnEnCO3lNGBkPMeYKxTsB5wp\n"
    "-----END RSA PRIVATE KEY-----\n"
    "ดู error log แล้วไม่เข้าใจครับ"
)

# Cyrillic homoglyphs + zero-width spaces + base64
MSG4_BYPASS = (
    "ลองดูข้อมูลนี้หน่อย:\n"
    "1. รหัส: cGFzc3dvcmQ9UEBzc3cwcmQhMjAyNQ== "
    "(base64 ของ password=P@ssw0rd!2025)\n"
    "2. เลขบัตร 4532​0151​2345​6789 "
    "ช่วยเช็คให้หน่อย"
    "ว่าบัตรใบนี้ยังใช้ได้มั้ย\n"
    "3. connection: postgresql://admin:ѕ​е​с​r​е​t"
    "@internal.db:5432/prod"
)

MSG5_CLEAN = (
    "ช่วยอธิบายหลักการทำงานของ "
    "Transformer architecture ใน Large Language Models หน่อยครับ "
    "สนใจเรื่อง self-attention mechanism "
    "เป็นพิเศษ "
    "อยากเข้าใจว่าทำไม "
    "attention is all you need"
)

MSG6_ENGLISH = (
    "Employee report for Mr. John Smith, born 15/03/1990, SSN 123-45-6789. "
    "He is a practicing Muslim and attends the local mosque every Friday. "
    "He was diagnosed with depression last year and is currently on medication. "
    "Email: john.smith@company.com Phone: +1-555-012-3456"
)


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "label": "TC1: Thai Loan Application",
        "message": MSG1_LOAN,
        "expected_status": 200,
        "expected_action": "mask",
        "expected_detectors": [
            "thai_name", "thai_national_id", "thai_address",
            "phone", "email", "thai_bank_account", "thai_promptpay",
        ],
    },
    {
        "label": "TC2: HR Report / PDPA §26",
        "message": MSG2_HR,
        "expected_status": 200,
        "expected_action": "mask",
        "expected_detectors": [
            "thai_name", "date_of_birth", "thai_national_id",
            "religion_belief", "criminal_record", "health_condition",
            "biometric_data", "phone",
        ],
    },
    {
        "label": "TC3: Developer Credentials",
        "message": MSG3_CREDS,
        "expected_status": 403,
        "expected_action": "block",
        "expected_detectors": [
            "db_connection_string", "api_key_secret", "private_key",
        ],
    },
    {
        "label": "TC4: Encoding Bypass",
        "message": MSG4_BYPASS,
        "expected_status": 403,
        "expected_action": "block",
        "expected_detectors": [
            "credit_card", "db_connection_string",
        ],
    },
    {
        "label": "TC5: Clean Text",
        "message": MSG5_CLEAN,
        "expected_status": 200,
        "expected_action": "allow",
        "expected_detectors": [],
    },
    {
        "label": "TC6: English Muslim Employee",
        "message": MSG6_ENGLISH,
        "expected_status": 200,
        "expected_action": "mask",
        "expected_detectors": [
            "date_of_birth", "ssn", "religion_belief",
            "health_condition", "email", "phone",
        ],
    },
]


# ---------------------------------------------------------------------------
# Admin session
# ---------------------------------------------------------------------------


def admin_login() -> requests.Session:
    """Login as admin, return a Session with cookies."""
    s = requests.Session()
    resp = s.post(ADMIN_LOGIN_URL, json={
        "email": ADMIN_USER, "password": ADMIN_PASS,
    })
    if resp.status_code != 200:
        print(_safe(f"  [WARN] Admin login failed ({resp.status_code}). Traces will be unavailable."))
        return s
    return s


def fetch_trace(admin_session: requests.Session, request_id: str) -> dict | None:
    """Fetch the 4-stage trace from admin API."""
    url = f"{ADMIN_REQUESTS_URL}/{request_id}"
    try:
        resp = admin_session.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Gateway call
# ---------------------------------------------------------------------------


def send_to_gateway(message: str, model: str = "gpt-4o-mini") -> dict:
    """Send a message through the gateway and return response data."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "max_tokens": 150,
    }
    t0 = time.time()
    try:
        resp = requests.post(GATEWAY_URL, headers=HEADERS, json=payload, timeout=30)
    except requests.exceptions.ConnectionError:
        return {"status": -1, "error": "Connection refused - is gateway running?"}

    elapsed = int((time.time() - t0) * 1000)
    result: dict = {"status": resp.status_code, "elapsed_ms": elapsed}

    body = resp.json()
    if resp.status_code == 200:
        result["request_id"] = body.get("id", "")
        result["policy_action"] = body.get("policy_action", "?")
        result["policy_reasons"] = body.get("policy_reasons", [])
        result["model_used"] = body.get("model", "?")
        result["provider"] = body.get("provider", "?")
        content = (
            body.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        result["reply"] = content
    elif resp.status_code == 403:
        err = body.get("error", {})
        result["request_id"] = body.get("request_id", "")
        result["policy_action"] = "block"
        result["error_code"] = err.get("code", "?")
        result["error_message"] = err.get("message", "")
        details = err.get("details", {})
        result["blocked_detectors"] = details.get("detectors", [])
        result["blocked_reasons"] = details.get("reasons", [])
    else:
        result["raw"] = resp.text[:400]

    return result


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

W = 78  # box width


def _box_top():
    return "╔" + "═" * (W - 2) + "╗"


def _box_mid():
    return "╠" + "═" * (W - 2) + "╣"


def _box_bot():
    return "╚" + "═" * (W - 2) + "╝"


def _row(text: str):
    """Pad text to fit inside the box."""
    safe = _safe(text)
    pad = W - 4 - len(safe)
    if pad < 0:
        safe = safe[:W - 7] + "..."
        pad = 0
    return "║  " + safe + " " * pad + "║"


def _inner_top():
    return "  ┌" + "─" * (W - 6) + "┐"


def _inner_bot():
    return "  └" + "─" * (W - 6) + "┘"


def _inner_row(text: str):
    safe = _safe(text)
    pad = W - 8 - len(safe)
    if pad < 0:
        safe = safe[:W - 11] + "..."
        pad = 0
    return "  │ " + safe + " " * pad + " │"


def print_trace_block(title: str, text: str | None):
    """Print one of the 4 trace stages inside the box."""
    p = lambda s: print(_safe(s))
    p(_row(""))
    p(_row(f"● {title}"))
    p(_inner_top())
    if text:
        for line in text.split("\n")[:8]:
            p(_inner_row(_trunc(line, W - 12)))
        if text.count("\n") > 7:
            p(_inner_row(f"... ({text.count(chr(10)) + 1} lines total)"))
    else:
        p(_inner_row("(not available)"))
    p(_inner_bot())


def print_detections(findings: list[dict] | None, expected: list[str]):
    """Print detection table."""
    p = lambda s: print(_safe(s))
    p(_row(""))
    p(_row("DETECTIONS:"))

    if findings:
        p(_row(f"  {'Detector':<20} {'Severity':<10} {'Action':<8} {'Matched'}"))
        p(_row(f"  {'-'*20} {'-'*10} {'-'*8} {'-'*20}"))
        for f in findings:
            det = f.get("detector", "?")
            sev = f.get("severity", "?")
            act = f.get("action", "?")
            txt = _trunc(f.get("matched_text", f.get("mask_token", "?")), 25)
            p(_row(f"  {det:<20} {sev:<10} {act:<8} {txt}"))
    elif expected:
        p(_row("  (no findings in trace - checking policy_reasons)"))
    else:
        p(_row("  (no detections expected)"))

    if expected:
        found_detectors = {f.get("detector") for f in (findings or [])}
        detected = [d for d in expected if d in found_detectors]
        missing = [d for d in expected if d not in found_detectors]
        p(_row(f"  Detected: {len(detected)}/{len(expected)}"))
        if missing:
            p(_row(f"  Missing:  {missing}"))


def print_test_case(tc: dict, result: dict, trace: dict | None):
    """Print a single test case with full trace."""
    p = lambda s: print(_safe(s))
    label = tc["label"]
    expected_status = tc["expected_status"]
    expected_action = tc["expected_action"]
    expected_detectors = tc["expected_detectors"]

    # Header
    p(_box_top())
    status_str = str(result.get("status", "?"))
    action_str = result.get("policy_action", "?")
    p(_row(f"{label}"))
    p(_row(f"Model: gpt-4o-mini | Status: {status_str} | DLP: {action_str.upper()} | {result.get('elapsed_ms', 0)}ms"))
    p(_box_mid())

    if result.get("status") == -1:
        p(_row(f"ERROR: {result.get('error', 'unknown')}"))
        p(_box_bot())
        return

    if result.get("status") == 403:
        # Blocked — show original message and block reason
        print_trace_block("USER -> SERVICE (original message)", tc["message"])
        p(_row(""))
        p(_row(f"BLOCKED: {result.get('error_code', '?')}"))
        p(_row(f"Message: {_trunc(result.get('error_message', ''), W - 15)}"))
        reasons = result.get("blocked_reasons", [])
        if reasons:
            for r in reasons[:5]:
                p(_row(f"  - {_trunc(r, W - 10)}"))

        if trace:
            findings = trace.get("dlp_findings", [])
            print_detections(findings, expected_detectors)

        # Verdict
        p(_row(""))
        status_ok = result["status"] == expected_status
        action_ok = action_str == expected_action
        p(_row(f"Status: {'PASS' if status_ok else 'FAIL'} ({status_str} vs {expected_status})"))
        p(_row(f"Action: {'PASS' if action_ok else 'FAIL'} ({action_str} vs {expected_action})"))
        p(_row(f"Result: {'PASSED' if (status_ok and action_ok) else 'FAILED'}"))
        p(_box_bot())
        return

    # 200 response — show all 4 stages
    if trace:
        print_trace_block("USER -> SERVICE (original message)", trace.get("input_raw"))
        print_trace_block("SERVICE -> AI (masked -- sent to LLM)", trace.get("input_masked"))
        print_trace_block("AI -> SERVICE (LLM response with tokens)", trace.get("output_raw"))
        print_trace_block("SERVICE -> USER (restored -- returned to user)", trace.get("output_final"))

        findings = trace.get("dlp_findings", [])
        print_detections(findings, expected_detectors)
    else:
        # Fallback: show what we have from the gateway response
        print_trace_block("USER -> SERVICE (original message)", tc["message"])
        p(_row(""))
        reasons = result.get("policy_reasons", [])
        if reasons:
            p(_row("Policy Reasons:"))
            for r in reasons[:8]:
                p(_row(f"  - {_trunc(r, W - 10)}"))
        print_trace_block("SERVICE -> USER (AI response)", result.get("reply", ""))

    # Verdict
    p(_row(""))
    status_ok = result["status"] == expected_status
    action_ok = action_str == expected_action
    p(_row(f"Status: {'PASS' if status_ok else 'FAIL'} ({status_str} vs {expected_status})"))
    p(_row(f"Action: {'PASS' if action_ok else 'FAIL'} ({action_str} vs {expected_action})"))

    if expected_detectors and trace:
        found_dets = {f.get("detector") for f in (trace.get("dlp_findings") or [])}
        det_count = sum(1 for d in expected_detectors if d in found_dets)
        p(_row(f"Detectors: {det_count}/{len(expected_detectors)}"))

    passed = status_ok and action_ok
    p(_row(f"Result: {'PASSED' if passed else 'FAILED'}"))
    p(_box_bot())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(results: list[tuple[dict, dict, dict | None]]):
    """Print the final summary table."""
    p = lambda s: print(_safe(s))

    p("")
    p(_box_top())
    p(_row("DLP LIVE GATEWAY TEST SUMMARY"))
    p(_box_mid())
    p(_row(f"{'#':<4} {'Test Case':<28} {'Status':<8} {'Action':<8} {'Detected':<12} {'Result'}"))
    p(_row(f"{'='*4} {'='*28} {'='*8} {'='*8} {'='*12} {'='*8}"))

    total_pass = 0
    for i, (tc, result, trace) in enumerate(results, 1):
        status = str(result.get("status", "?"))
        action = result.get("policy_action", "?")
        expected_dets = tc["expected_detectors"]

        if trace and expected_dets:
            found_dets = {f.get("detector") for f in (trace.get("dlp_findings") or [])}
            det_count = sum(1 for d in expected_dets if d in found_dets)
            det_str = f"{det_count}/{len(expected_dets)}"
        elif action == "block" and expected_dets:
            blocked_dets = result.get("blocked_detectors", [])
            if blocked_dets:
                det_count = sum(1 for d in expected_dets if d in blocked_dets)
                det_str = f"{det_count}/{len(expected_dets)}"
            else:
                det_str = "BLK"
        elif not expected_dets:
            det_str = "0/0"
        else:
            det_str = "?/?"

        status_ok = result.get("status") == tc["expected_status"]
        action_ok = result.get("policy_action") == tc["expected_action"]
        passed = status_ok and action_ok
        if passed:
            total_pass += 1
        verdict = "PASS" if passed else "FAIL"

        label = tc["label"][:28]
        p(_row(f"{i:<4} {label:<28} {status:<8} {action:<8} {det_str:<12} {verdict}"))

    total = len(results)
    p(_box_mid())
    p(_row(f"TOTAL: {total_pass}/{total} tests passed"))

    if total_pass == total:
        p(_row("All tests passed!"))
    else:
        p(_row(f"{total - total_pass} test(s) failed."))

    p(_row(""))
    p(_row("Check full request traces at: http://localhost:3001/requests"))
    p(_box_bot())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    p = lambda s: print(_safe(s))

    p(_box_top())
    p(_row("LIVE DLP GATEWAY TEST WITH FULL TRACE"))
    p(_row(f"Gateway : {GATEWAY_URL}"))
    p(_row(f"API Key : {API_KEY[:12]}..."))
    p(_row(f"Model   : gpt-4o-mini"))
    p(_box_bot())

    # Health check
    try:
        health = requests.get(f"{BASE_URL}/health", timeout=5)
        p(f"\n  Health check: {health.json().get('status', '?')}")
    except Exception as e:
        p(f"\n  [ERROR] Gateway not reachable: {e}")
        p("  Start the gateway first: uvicorn app.main:app --port 8080")
        sys.exit(1)

    # Admin login for trace fetching
    p("  Logging in as admin for trace access...")
    admin_sess = admin_login()

    results: list[tuple[dict, dict, dict | None]] = []

    for tc in TEST_CASES:
        p(f"\n  Sending: {tc['label']}...")
        result = send_to_gateway(tc["message"])

        # Fetch trace from admin API
        trace = None
        request_id = result.get("request_id")
        if request_id:
            time.sleep(0.5)
            trace = fetch_trace(admin_sess, request_id)

        print_test_case(tc, result, trace)
        results.append((tc, result, trace))
        time.sleep(1)

    print_summary(results)

    all_passed = all(
        r.get("status") == tc["expected_status"]
        and r.get("policy_action") == tc["expected_action"]
        for tc, r, _ in results
    )
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
