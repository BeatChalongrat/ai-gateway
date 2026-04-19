"""DLP complex-sentence test — 5 natural-language scenarios.

Each case embeds several sensitive values inside a realistic Thai-language
narrative (booking confirmation, job application, incident report, …) and
asks the AI to **summarize** the content. A summary task is materially
different from an "echo bot" task: the model naturally references the
values in its reply, which forces the full loop —

    USER    -> SERVICE : narrative + raw values
    SERVICE -> AI      : narrative + [DETECTOR_N] tokens
    AI      -> SERVICE : summary that cites the tokens in context
    SERVICE -> USER    : summary with tokens restored to raw values

Pass criteria per case (AND'd):
- ``inbound_ok``   — no raw value appears in ``input_masked``
- ``outbound_ok``  — every raw value (full, or its first 8+ chars for
                      long values like connection strings) appears in
                      ``output_final``. Summaries paraphrase; as long as
                      the *values themselves* survive the round-trip we
                      count the loop as closed.
- ``policy_ok``    — ``policy_action == "mask"``
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GATEWAY = "http://localhost:8080"
ADMIN_KEY_ENV = "NEXT_PUBLIC_ADMIN_API_KEY"
REDIS_CONTAINER = "ai-gateway-redis-1"


# ---------------------------------------------------------------------------
# Setup — mirrors scripts/tests/dlp_full_loop_test.py
# ---------------------------------------------------------------------------


def load_admin_key() -> str:
    key = os.environ.get(ADMIN_KEY_ENV)
    if key:
        return key
    env_file = Path(__file__).resolve().parents[2] / "admin-ui" / ".env.local"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(ADMIN_KEY_ENV + "="):
                return line.split("=", 1)[1].strip()
    sys.exit(f"error: admin key not found (set {ADMIN_KEY_ENV} or put in admin-ui/.env.local)")


def flush_response_cache() -> None:
    try:
        out = subprocess.run(
            ["docker", "exec", REDIS_CONTAINER, "redis-cli", "eval",
             "local k=redis.call('keys','cache:*'); for i=1,#k do redis.call('del',k[i]) end; return #k",
             "0"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            print(f"[setup] flushed {out.stdout.strip()} cache:* keys")
        else:
            print(f"[setup] cache flush skipped: {out.stderr.strip()[:120]}")
    except Exception as exc:
        print(f"[setup] cache flush skipped: {exc}")


def pick_default_policy_project(client: httpx.Client, headers: dict) -> dict:
    r = client.get(f"{GATEWAY}/admin/projects", headers=headers)
    r.raise_for_status()
    for p in r.json().get("items", []):
        if p.get("policy_profile_id") == "default":
            return p
    sys.exit("error: no project with policy_profile_id='default' found")


def ensure_ner_ready(client: httpx.Client, headers: dict, timeout_s: float = 60.0) -> str:
    """Turn NER on at the org level and wait for the analyzer to load.

    The default ``org_settings.ner_enabled`` is False, so
    ``PresidioDetector.detect()`` returns [] silently. Case 2 relies on
    ``presidio_ner`` firing on "John Smith", so this test enables NER
    before the run and polls ``/admin/ner/status`` until the background
    loader reports ``ready``. Returns the final status string.
    """
    client.patch(f"{GATEWAY}/admin/settings", headers=headers, json={"ner_enabled": True})
    deadline = time.time() + timeout_s
    last = "unknown"
    while time.time() < deadline:
        try:
            r = client.get(f"{GATEWAY}/admin/ner/status", headers=headers)
            if r.status_code == 200:
                body = r.json()
                last = body.get("status", "unknown")
                if last in ("ready", "error"):
                    return last
        except Exception:
            pass
        time.sleep(1.0)
    return last


def create_temp_key(client: httpx.Client, headers: dict, project: dict) -> dict:
    r = client.post(
        f"{GATEWAY}/admin/api-keys",
        headers=headers,
        json={
            "tenant_id": project["tenant_id"],
            "project_id": project["project_id"],
            "name": "dlp-complex-test",
        },
    )
    r.raise_for_status()
    return r.json()


def revoke_key(client: httpx.Client, headers: dict, key_id: str) -> None:
    try:
        client.delete(f"{GATEWAY}/admin/api-keys/{key_id}", headers=headers)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class Case:
    n: int
    name: str
    detectors: tuple[str, ...]
    # Values that must be hidden inbound and come back outbound. For long
    # strings (connection URLs, JWTs), the partial prefix is enough — the
    # AI may paraphrase but should not re-invent the full value.
    raw_values: list[dict] = field(default_factory=list)
    message: str = ""


# Each raw_value entry: {"value": full raw string, "min_len": min prefix
# length we will accept as a match in the output_final}. When "min_len"
# is missing we try the full value first, then a 10-char prefix, then 8.
# Keeping ``check_value`` separate lets us be flexible on long strings
# without weakening the inbound leak check, which always uses the full
# value.

CASES: list[Case] = [
    Case(
        1,
        "ระบบจองตั๋ว (passport + email + credit_card)",
        ("passport", "email", "credit_card"),
        raw_values=[
            {"value": "AA1234567"},
            {"value": "somchai@company.co.th"},
            {"value": "4532015112830366"},
        ],
        message=(
            "ระบบจองตั๋วเที่ยวบิน TG308 ได้รับข้อมูลผู้โดยสารเรียบร้อยแล้ว\n"
            "ชื่อ: นายสมชาย ใจดี\n"
            "หนังสือเดินทางเลขที่ AA1234567 หมดอายุ 31 ธันวาคม 2028\n"
            "กรุณายืนยันการจองได้ที่อีเมล somchai@company.co.th\n"
            "ค่าบริการ 15,000 บาทได้ถูกเรียกเก็บจากบัตรเครดิต 4532015112830366 เรียบร้อยแล้ว\n"
            "กรุณาสรุปรายละเอียดการจองนี้ให้ครบถ้วน รวมหมายเลขและอีเมลที่ระบุด้วย [tag:{hex}]"
        ),
    ),
    Case(
        2,
        "ใบสมัครงาน (presidio_ner + dob + phone + ssn)",
        ("presidio_ner", "date_of_birth", "phone", "ssn"),
        raw_values=[
            {"value": "John Smith"},
            {"value": "15/03/1990"},
            {"value": "081-234-5678"},
            {"value": "123-45-6789"},
        ],
        message=(
            "ใบสมัครงานตำแหน่ง Software Engineer ของ John Smith\n"
            "วันเกิด 15/03/1990 สัญชาติอเมริกัน\n"
            "Social Security Number: 123-45-6789\n"
            "สามารถติดต่อได้ที่เบอร์ 081-234-5678 ทุกวันทำการ\n"
            "มีประสบการณ์ด้าน Python และ FastAPI มากกว่า 5 ปี\n"
            "เคยทำงานที่ Google และ Meta มาก่อน\n"
            "กรุณาสรุปประวัติผู้สมัครนี้ให้กระชับ พร้อมระบุเบอร์ วันเกิด และเลข SSN ตามต้นฉบับ [tag:{hex}]"
        ),
    ),
    Case(
        3,
        "Incident DevOps (api_key + db_conn + bearer + password)",
        ("api_key_secret", "db_connection_string", "bearer_token", "password_field"),
        raw_values=[
            # Long values — partial prefix is acceptable for outbound match.
            {"value": "sk-ant-api03-xK9mN2pL8qR5vT3wY6uI1oE4aS7dF0jHmQbZcVnXp12345678901234",
             "partials": ["sk-ant-api03-", "sk-ant-api03"]},
            {"value": "postgresql://admin:P@ssw0rd123@db.internal:5432/prod",
             "partials": ["postgresql://admin", "db.internal:5432"]},
            {"value": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMSJ9.abc123",
             "partials": ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"]},
            {"value": "Sup3rS3cr3t!"},
        ],
        message=(
            "รายงาน Incident #4521 เวลา 02:35 น. ระบบ Production ล่ม\n"
            "สาเหตุ: มีการ push config ที่มี credentials โดยไม่ตั้งใจ\n"
            "พบ API Key: sk-ant-api03-xK9mN2pL8qR5vT3wY6uI1oE4aS7dF0jHmQbZcVnXp12345678901234 ใน public repo\n"
            "Database: postgresql://admin:P@ssw0rd123@db.internal:5432/prod ถูก expose\n"
            "Service token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMSJ9.abc123 ยังใช้งานอยู่\n"
            "Admin portal ยังใช้ password=Sup3rS3cr3t! อยู่ ต้องเปลี่ยนด่วน\n"
            "กรุณาสรุป action items ที่ต้องทำเพื่อแก้ไข incident นี้ อ้างอิง credentials ต่างๆ ที่เจอด้วย [tag:{hex}]"
        ),
    ),
    Case(
        4,
        "เปิดบัญชี (thai_nid + tax_id + bank + address + promptpay)",
        ("thai_national_id", "thai_tax_id", "thai_bank_account", "thai_address", "thai_promptpay"),
        raw_values=[
            {"value": "1-1001-00001-00-6"},
            {"value": "1234567890121"},
            {"value": "123-4-56789-0"},
            # Address captured by detector is usually a substring starting
            # with the house number. Accept the street keyword as partial.
            {"value": "123 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ 10110",
             "partials": ["ถนนสุขุมวิท", "สุขุมวิท"]},
            {"value": "081-234-5678"},
        ],
        message=(
            "แบบฟอร์มขอเปิดบัญชีออมทรัพย์\n"
            "ชื่อ-นามสกุล: นางสาวรัตนา มีสุข อายุ 32 ปี อาชีพ พนักงานบริษัท\n"
            "เลขบัตรประชาชน: 1-1001-00001-00-6\n"
            "เลขประจำตัวผู้เสียภาษี: 1234567890121\n"
            "ที่อยู่ปัจจุบัน: 123 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ 10110\n"
            "บัญชีธนาคารเดิม: 123-4-56789-0 ธนาคารกสิกรไทย\n"
            "PromptPay ผูกกับเบอร์ 081-234-5678\n"
            "รายได้ต่อเดือน 45,000 บาท ต้องการฝากเงินเดือนละ 10,000 บาท\n"
            "กรุณาสรุปข้อมูลผู้ขอเปิดบัญชีนี้ ระบุหมายเลขทุกตัวและที่อยู่ตามต้นฉบับ [tag:{hex}]"
        ),
    ),
    Case(
        5,
        "IT โรงพยาบาล (email + ip + mac + medical + iban + vehicle_plate)",
        ("email", "ip_address", "mac_address", "medical_record", "iban", "vehicle_plate"),
        raw_values=[
            {"value": "alice@company.co.th"},
            {"value": "192.168.1.100"},
            {"value": "00:1A:2B:3C:4D:5E"},
            {"value": "0012345"},
            {"value": "GB29NWBK60161331926819"},
            {"value": "กข 1234"},
        ],
        message=(
            "รายงานการตรวจสอบระบบ IT โรงพยาบาลกรุงเทพ ประจำเดือนเมษายน 2026\n"
            "ผู้รับผิดชอบ: ดร.วิชัย สุขสมบูรณ์ ติดต่อ alice@company.co.th\n"
            "เซิร์ฟเวอร์หลักทำงานที่ IP: 192.168.1.100 เสถียรภาพ 99.9%\n"
            "อุปกรณ์ Medical IoT MAC Address: 00:1A:2B:3C:4D:5E ต้องอัปเดต firmware\n"
            "ผู้ป่วย HN: 0012345 วินิจฉัย ICD-10 E11.9 นัดติดตามอาการเดือนหน้า\n"
            "ค่าใช้จ่ายโอนผ่าน IBAN: GB29NWBK60161331926819 จากต่างประเทศ\n"
            "รถพยาบาลทะเบียน กข 1234 กรุงเทพ อยู่ระหว่างซ่อมบำรุง\n"
            "กรุณาสรุปประเด็นสำคัญจากรายงานนี้ ระบุ email, IP, MAC, HN, IBAN และทะเบียนรถตามต้นฉบับ [tag:{hex}]"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Per-case execution + verdict
# ---------------------------------------------------------------------------


def fetch_trace(client: httpx.Client, admin_headers: dict, request_id: str | None) -> dict | None:
    if not request_id:
        return None
    for _ in range(5):
        try:
            r = client.get(f"{GATEWAY}/admin/requests/{request_id}", headers=admin_headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                time.sleep(0.4)
                continue
        except Exception:
            time.sleep(0.4)
    return None


def run_case(client: httpx.Client, api_key: str, case: Case) -> tuple[dict, str]:
    hex_tag = secrets.token_hex(4)
    content = case.message.replace("{hex}", hex_tag)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
    }
    r = client.post(f"{GATEWAY}/v1/chat/completions", headers=headers, json=body, timeout=120.0)
    return r.json(), hex_tag


def _acceptable_forms(entry: dict) -> list[str]:
    """Return progressively shorter forms to try for outbound matching."""
    full = entry["value"]
    partials = entry.get("partials", [])
    forms = [full] + list(partials)
    # Auto-generate trimmed forms for long strings (≥14 chars) if the
    # entry didn't specify explicit partials.
    if not partials and len(full) >= 14:
        forms.extend([full[:10], full[:8]])
    return forms


def evaluate(case: Case, trace: dict | None) -> dict:
    v: dict = {
        "inbound_ok": False,
        "outbound_ok": False,
        "policy_ok": False,
        "policy_action": None,
        "tokens_input": 0,
        "per_value": [],
        "leaked_values": [],
        "missing_in_output": [],
        "notes": [],
    }
    if trace is None:
        v["notes"].append("trace fetch returned None")
        return v

    v["policy_action"] = trace.get("policy_action") or "-"
    v["policy_ok"] = v["policy_action"] == "mask"

    input_masked = trace.get("input_masked") or ""
    output_final = trace.get("output_final") or ""

    # Inbound: every raw_value must be absent from input_masked.
    inbound_ok = True
    for entry in case.raw_values:
        raw = entry["value"]
        leaked = raw in input_masked
        v["per_value"].append({"value": raw, "leaked_inbound": leaked})
        if leaked:
            inbound_ok = False
            v["leaked_values"].append(raw)
    v["inbound_ok"] = inbound_ok

    # Outbound: each raw_value's full form (or an accepted partial) should
    # appear in output_final. Summaries paraphrase — partials accommodate
    # truncated mentions ("sk-ant-api03-…") without weakening the check.
    outbound_ok = True
    for entry in case.raw_values:
        forms = _acceptable_forms(entry)
        if not any(form and form in output_final for form in forms):
            outbound_ok = False
            v["missing_in_output"].append(entry["value"])
    v["outbound_ok"] = outbound_ok

    findings = trace.get("dlp_findings") or []
    v["tokens_input"] = sum(
        1 for f in findings if isinstance(f, dict) and f.get("direction") == "input"
    )
    # Non-PASS drift signal: record which expected detectors fired so the
    # summary can flag when overlap resolution took a non-expected one.
    got_detectors = sorted({
        f.get("detector") for f in findings
        if isinstance(f, dict) and f.get("direction") == "input" and f.get("detector")
    })
    expected = set(case.detectors)
    missing_detectors = [d for d in expected if d not in got_detectors]
    if missing_detectors:
        v["notes"].append(
            f"expected detectors missing from trace: {missing_detectors} "
            f"(got {got_detectors})"
        )
    v["detectors_got"] = got_detectors

    return v


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_summary(rows: list[tuple[Case, dict, dict | None]]) -> None:
    print("\n=== DLP Complex Sentence Test — Natural Language ===\n")
    for case, v, _trace in rows:
        passed = v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"]
        icon = "PASS" if passed else "FAIL"
        print(f"[{icon}] Case {case.n} — {case.name}")
        print(
            f"       Inbound: {'OK' if v['inbound_ok'] else 'LEAK'} | "
            f"Outbound: {'OK' if v['outbound_ok'] else 'MISS'} | "
            f"Policy: {v['policy_action']} | "
            f"Tokens masked: {v['tokens_input']}"
        )
        if v["detectors_got"]:
            print(f"       Detectors fired: {','.join(v['detectors_got'])}")
        for note in v["notes"]:
            print(f"       note: {note}")
        print()

    passed = sum(1 for _, v, _ in rows if v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"])
    total = len(rows)
    flag = "PASS" if passed == total else "FAIL"
    print(f"=== FINAL: {passed}/{total} {flag} ===")


def print_fail_details(rows: list[tuple[Case, dict, dict | None]]) -> None:
    fails = [
        (c, v, t)
        for c, v, t in rows
        if not (v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"])
    ]
    if not fails:
        return
    print("\n=== FAIL details ===")
    for case, v, trace in fails:
        print(f"\n--- Case {case.n}: {case.name} ---")
        if v["leaked_values"]:
            print(f"  INBOUND LEAK — values still in input_masked: {v['leaked_values']}")
        if v["missing_in_output"]:
            print(f"  OUTBOUND MISS — values (or partials) not in output_final: {v['missing_in_output']}")
        if not v["policy_ok"]:
            print(f"  POLICY — expected 'mask', got {v['policy_action']!r}")
        if trace:
            print(f"  input_raw    : {(trace.get('input_raw') or '')[:250]}")
            print(f"  input_masked : {(trace.get('input_masked') or '')[:250]}")
            print(f"  output_raw   : {(trace.get('output_raw') or '')[:250]}")
            print(f"  output_final : {(trace.get('output_final') or '')[:500]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    admin_key = load_admin_key()
    admin_headers = {"Authorization": f"Bearer {admin_key}"}
    flush_response_cache()

    with httpx.Client() as client:
        project = pick_default_policy_project(client, admin_headers)
        print(
            f"[setup] project: {project['project_id']} ({project['name']}) "
            f"policy={project['policy_profile_id']}"
        )
        ner_status = ensure_ner_ready(client, admin_headers)
        print(f"[setup] NER analyzer status: {ner_status}")
        if ner_status != "ready":
            print(
                "[setup] WARNING: NER not ready — Case 2 will likely fail on "
                "'John Smith' because presidio_ner will return no matches."
            )
        created = create_temp_key(client, admin_headers, project)
        raw_key = created["key"]
        key_id = created["key_id"]
        print(f"[setup] temp api key prefix={created.get('key_prefix')}\n")

        rows: list[tuple[Case, dict, dict | None]] = []
        try:
            for case in CASES:
                try:
                    payload, _tag = run_case(client, raw_key, case)
                except Exception as exc:
                    print(f"  case {case.n} {case.name} -> NETWORK ERROR: {exc}")
                    rows.append((case, {
                        "inbound_ok": False, "outbound_ok": False, "policy_ok": False,
                        "policy_action": None, "tokens_input": 0, "per_value": [],
                        "leaked_values": [], "missing_in_output": [], "notes": [str(exc)],
                        "detectors_got": [],
                    }, None))
                    continue
                req_id = payload.get("id") if isinstance(payload, dict) else None
                # Give the orchestrator's fresh-session DB write a beat so
                # the admin GET doesn't race the commit.
                time.sleep(1.0)
                trace = fetch_trace(client, admin_headers, req_id)
                v = evaluate(case, trace)
                rows.append((case, v, trace))
                passed = v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"]
                mark = "PASS" if passed else "FAIL"
                print(
                    f"  case {case.n} {case.name[:60]:<60} "
                    f"in={v['inbound_ok']!s:<5} out={v['outbound_ok']!s:<5} -> {mark}"
                )
        finally:
            revoke_key(client, admin_headers, key_id)
            print(f"\n[cleanup] revoked key {key_id}")

    print_summary(rows)
    print_fail_details(rows)

    passed = sum(1 for _, v, _ in rows if v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"])
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
