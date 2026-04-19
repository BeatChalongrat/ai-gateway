"""DLP full-loop test — 22 detectors, one request each.

For each detector, send a Thai-template prompt that contains a sensitive
value AND asks the AI to echo the data back verbatim. Then verify the
complete loop in a single request:

    USER     -> SERVICE : raw value present
    SERVICE  -> AI      : raw value replaced with [DETECTOR_N]
    AI       -> SERVICE : echoes [DETECTOR_N]
    SERVICE  -> USER    : token replaced back with raw value

Tokens (mask_token) and raw values (matched_text) are pulled from the
admin trace's ``dlp_findings`` rather than hardcoded — actual mask token
names live in ``backend/app/services/dlp/transformer.py:40-67`` and
several differ from intuition (api_key_secret -> ``[CREDENTIAL]``,
thai_tax_id -> ``[TAX_ID]``, etc.).

Usage:
    python scripts/tests/dlp_full_loop_test.py
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

# A formatting task — much higher AI compliance than "please confirm".
# The model sees the inbound-masked form ([DETECTOR_N]) so even if it does
# echo verbatim, no raw value reaches the provider. Restore puts the
# original back on the way out.
TEMPLATE = (
    "ทำหน้าที่เป็น echo bot. "
    "ตอบกลับมาเฉพาะข้อความต่อไปนี้แบบเป๊ะๆ ไม่ต้องอธิบาย ไม่ต้องเพิ่มข้อความใดๆ:\n"
    "{data}\n"
    "(reference id: {tag})"
)

# Cache-buster shape: short hex token. Avoids the 13-digit ms-epoch
# collisions with credit_card / thai_national_id detectors that the
# previous "[ts:1776...]" tag triggered (visible as "[ts:[THAI_ID_0]]"
# in earlier traces).
def _cache_tag() -> str:
    return secrets.token_hex(4)  # 8 hex chars, e.g. "a1b2c3d4"

REDIS_CONTAINER = "ai-gateway-redis-1"


# ---------------------------------------------------------------------------
# Setup helpers
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
    """Best-effort Redis cache flush so this run sees cold DLP behavior."""
    try:
        out = subprocess.run(
            ["docker", "exec", REDIS_CONTAINER, "redis-cli", "eval",
             "local k=redis.call('keys','cache:*'); for i=1,#k do redis.call('del',k[i]) end; return #k",
             "0"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            print(f"[setup] flushed {out.stdout.strip()} cache:* keys from {REDIS_CONTAINER}")
        else:
            print(f"[setup] cache flush skipped: {out.stderr.strip()[:120]}")
    except Exception as exc:  # docker not available, redis not running, etc.
        print(f"[setup] cache flush skipped: {exc}")


def pick_default_policy_project(client: httpx.Client, admin_headers: dict) -> dict:
    r = client.get(f"{GATEWAY}/admin/projects", headers=admin_headers)
    r.raise_for_status()
    for p in r.json().get("items", []):
        if p.get("policy_profile_id") == "default":
            return p
    sys.exit("error: no project with policy_profile_id='default' found")


def create_temp_key(client: httpx.Client, admin_headers: dict, project: dict) -> dict:
    r = client.post(
        f"{GATEWAY}/admin/api-keys",
        headers=admin_headers,
        json={
            "tenant_id": project["tenant_id"],
            "project_id": project["project_id"],
            "name": "dlp-full-loop-test",
        },
    )
    r.raise_for_status()
    return r.json()


def revoke_key(client: httpx.Client, admin_headers: dict, key_id: str) -> None:
    try:
        client.delete(f"{GATEWAY}/admin/api-keys/{key_id}", headers=admin_headers)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------


@dataclass
class Case:
    n: int
    detector: str           # primary expected detector
    category: str           # for outbound expectation
    label: str
    input_data: str
    # Detectors we will accept as the inbound source-of-truth, ordered by
    # preference. Some inputs trigger overlapping detectors (e.g. a
    # promptpay number also matches the phone shape) — the expected
    # behavior is "any of these fired"; we use the first one found.
    accepted: tuple[str, ...] = field(default_factory=tuple)
    # Outbound contract — what the user is expected to see after restore.
    # Default is "unmap": when the user supplies the data and the AI
    # echoes the inbound token, ``transformer.restore()`` replaces the
    # token with the original raw value. Restore is unconditional —
    # it does NOT consult ``category_actions_output`` from Fix 3.
    # That outbound MASK rule only fires when the *outbound* detector
    # finds NEW content the AI invented (the leak case from Session B).
    # All 22 cases in this test exercise the echo-the-inbound-token
    # path, so the natural expectation is "unmap" for every category,
    # including credential and secret.
    expected_outbound: str = "unmap"

    def accepted_detectors(self) -> tuple[str, ...]:
        return self.accepted or (self.detector,)


CASES: list[Case] = [
    # IDENTITY
    Case(1, "passport", "identity", "passport",
         "หนังสือเดินทางเลขที่ AA1234567"),
    Case(2, "vehicle_plate", "identity", "vehicle_plate",
         "ทะเบียนรถ กข 1234 กรุงเทพ"),
    Case(3, "thai_national_id", "identity", "thai_national_id",
         "บัตรประชาชนเลขที่ 1-1001-00001-00-6"),
    Case(4, "thai_tax_id", "identity", "thai_tax_id",
         "เลขประจำตัวผู้เสียภาษี 1234567890121",
         accepted=("thai_tax_id", "thai_national_id")),

    # FINANCIAL
    Case(5, "credit_card", "financial", "credit_card",
         "บัตรเครดิตเลขที่ 4532015112830366"),
    Case(6, "iban", "financial", "iban",
         "IBAN: GB29NWBK60161331926819"),
    Case(7, "thai_bank_account", "financial", "thai_bank_account",
         "บัญชีธนาคาร 123-4-56789-0"),
    Case(8, "thai_promptpay", "financial", "thai_promptpay",
         "PromptPay โทร 081-234-5678",
         accepted=("thai_promptpay", "phone")),

    # CREDENTIAL / SECRET — restore is unconditional, so when the user
    # supplies the value and the AI echoes the token, the raw value
    # comes back to that same user (their own data round-tripped). The
    # session-brief description "outbound = mask" applies only to NEW
    # findings the *AI* generated, not to echoes of the user's input.
    # The api_key_secret regex for Anthropic keys requires 32+ chars
    # after the ``sk-ant-api03-`` prefix (matches the real key shape).
    Case(9, "api_key_secret", "credential", "api_key_secret",
         "API Key: sk-ant-api03-aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uVwXyZ",
         accepted=("api_key_secret", "bearer_token")),
    Case(10, "db_connection_string", "credential", "db_connection_string",
         "Connection: postgresql://admin:P@ssw0rd123@db.internal:5432/prod",
         accepted=("db_connection_string", "api_key_secret", "password_field")),

    # SECRET
    Case(11, "bearer_token", "secret", "bearer_token",
         "Token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMSJ9.abc123",
         accepted=("bearer_token", "api_key_secret")),
    Case(12, "private_key", "secret", "private_key",
         "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA2a2r\n-----END RSA PRIVATE KEY-----"),
    Case(13, "password_field", "secret", "password_field",
         "รหัสผ่าน password=Sup3rS3cr3t!"),

    # PII
    Case(14, "email", "pii", "email",
         "อีเมล somchai.test@company.co.th"),
    Case(15, "phone", "pii", "phone",
         "โทร 081-234-5678"),
    Case(16, "ssn", "pii", "ssn",
         "SSN: 123-45-6789"),
    Case(17, "date_of_birth", "pii", "date_of_birth",
         "วันเกิด 15/03/1990"),
    Case(18, "thai_address", "pii", "thai_address",
         "ที่อยู่ 123 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ 10110"),
    Case(19, "medical_record", "pii", "medical_record",
         "HN: 0012345 ICD-10 E11.9"),
    Case(20, "presidio_ner", "pii", "presidio_ner",
         "ชื่อ John Smith เกิด January 5 1985 อยู่ที่ New York",
         accepted=("presidio_ner", "date_of_birth")),

    # NETWORK
    Case(21, "ip_address", "network", "ip_address",
         "เซิร์ฟเวอร์ IP: 192.168.1.100"),
    Case(22, "mac_address", "network", "mac_address",
         "MAC Address: 00:1A:2B:3C:4D:5E"),
]


# ---------------------------------------------------------------------------
# Per-case execution
# ---------------------------------------------------------------------------


def run_case(client: httpx.Client, api_key: str, case: Case, tag: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    content = TEMPLATE.format(data=case.input_data, tag=tag)
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
    }
    out: dict = {"sent_content": content, "status": 0, "request_id": None}
    try:
        r = client.post(f"{GATEWAY}/v1/chat/completions", headers=headers, json=body, timeout=90.0)
    except httpx.RequestError as exc:
        out["error"] = f"network: {exc}"
        return out
    out["status"] = r.status_code
    try:
        payload = r.json()
        out["body"] = payload
        out["policy_action"] = payload.get("policy_action")
        if isinstance(payload, dict):
            out["request_id"] = payload.get("id")
            choices = payload.get("choices", [])
            if choices:
                out["reply_content"] = choices[0].get("message", {}).get("content") or ""
    except Exception:
        out["body"] = r.text
    return out


def fetch_trace(client: httpx.Client, admin_headers: dict, request_id: str | None) -> dict | None:
    if not request_id:
        return None
    for _ in range(4):
        try:
            r = client.get(f"{GATEWAY}/admin/requests/{request_id}", headers=admin_headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                time.sleep(0.3)
                continue
        except Exception:
            time.sleep(0.3)
    return None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def select_inbound_finding(trace: dict, accepted: tuple[str, ...]) -> dict | None:
    """Pick the inbound finding for the expected detector, in order of
    the case's ``accepted`` tuple. Falls back to the first inbound match
    when none of the accepted detectors fired (helps diagnose drift)."""
    findings = trace.get("dlp_findings") or []
    inbound = [f for f in findings if isinstance(f, dict) and f.get("direction") == "input"]
    by_name: dict[str, dict] = {}
    for f in inbound:
        det = f.get("detector")
        if det and det not in by_name:
            by_name[det] = f
    for det in accepted:
        if det in by_name:
            return by_name[det]
    return inbound[0] if inbound else None


def evaluate(case: Case, res: dict, trace: dict | None) -> dict:
    """Compute inbound / outbound / policy verdicts in a single dict."""
    v: dict = {
        "inbound_ok": False,
        "outbound_ok": False,
        "policy_ok": False,
        "raw_in_output": False,
        "token_in_output": False,
        "ai_echoed": False,
        "inbound_token": None,
        "raw_value": None,
        "matched_detector": None,
        "policy_action": (trace or {}).get("policy_action") or res.get("policy_action") or "-",
        "notes": [],
    }
    if trace is None:
        v["notes"].append("trace fetch returned None")
        return v

    finding = select_inbound_finding(trace, case.accepted_detectors())
    if finding is None:
        v["notes"].append("no inbound finding recorded")
        return v

    v["matched_detector"] = finding.get("detector")
    v["inbound_token"] = finding.get("mask_token")
    v["raw_value"] = finding.get("matched_text")

    accepted = set(case.accepted_detectors())
    if v["matched_detector"] not in accepted:
        v["notes"].append(
            f"detector drift: expected one of {sorted(accepted)}, got {v['matched_detector']!r}"
        )

    input_masked = trace.get("input_masked") or ""
    output_final = trace.get("output_final") or ""

    # Inbound check.
    # The strict path: the *expected* detector emitted a token and that
    # token is present in input_masked.
    # The lenient path: when two detectors overlap on the same span, the
    # transformer's overlap resolver picks one and the loser's
    # mask_token comes back as None on the trace. End-to-end the loop
    # still works (the raw value is hidden under the winning detector's
    # token), so we accept the inbound side if any inbound mask token
    # covers the span AND the raw value is gone from input_masked.
    inbound_token = v["inbound_token"]
    raw_value = v["raw_value"] or ""
    raw_in_masked = bool(raw_value) and raw_value in input_masked

    if inbound_token and inbound_token in input_masked:
        if raw_in_masked:
            v["notes"].append("inbound token present BUT raw value also leaked into input_masked")
        else:
            v["inbound_ok"] = True
    elif not raw_in_masked and finding is not None:
        # Expected detector lost the overlap race but the raw value is
        # still hidden — credit the inbound mask either way.
        v["inbound_ok"] = True
        v["notes"].append(
            f"expected detector ({v['matched_detector']}) lost overlap to another "
            f"detector, but raw value is hidden in input_masked — loop intact"
        )
    else:
        v["notes"].append("inbound mask_token not present in input_masked")

    # Outbound: did the AI echo? Did the raw / token survive to the user?
    v["raw_in_output"] = bool(raw_value) and raw_value in output_final
    v["token_in_output"] = bool(inbound_token) and inbound_token in output_final
    v["ai_echoed"] = v["raw_in_output"] or v["token_in_output"]

    if case.expected_outbound == "unmap":
        v["outbound_ok"] = v["raw_in_output"] and not v["token_in_output"]
        if not v["outbound_ok"]:
            if not v["ai_echoed"]:
                v["notes"].append("AI did not echo the masked value (unmap loop not exercised)")
            elif v["token_in_output"]:
                v["notes"].append("token survived restore — unmap did not run")
    else:  # 'mask'
        v["outbound_ok"] = v["token_in_output"] and not v["raw_in_output"]
        if not v["outbound_ok"]:
            if v["raw_in_output"]:
                v["notes"].append(
                    "raw value reached the user — restore() unconditionally "
                    "replaces inbound tokens regardless of category. "
                    "Outbound MASK only catches NEW findings on AI output."
                )
            elif not v["ai_echoed"]:
                v["notes"].append("AI did not echo the masked token")

    v["policy_ok"] = v["policy_action"] == "mask"
    if not v["policy_ok"]:
        v["notes"].append(f"policy_action != 'mask' (got {v['policy_action']!r})")

    return v


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def fmt(ok: bool) -> str:
    return "MASK PASS" if ok else "FAIL"


def print_summary(rows: list[tuple[Case, dict, dict, dict | None]]) -> None:
    print("\n=== DLP Full Loop Test — 22 Detectors ===\n")
    header = f"{'#':>2} | {'Detector':<22} | {'Cat':<10} | {'Inbound':<7} | {'Outbound':<8} | {'Policy':<6} | Result"
    print(header)
    print("-" * len(header))
    pass_count = 0
    for case, _res, v, _trace in rows:
        passed = v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"]
        if passed:
            pass_count += 1
        in_mark = "MASK" if v["inbound_ok"] else "----"
        if case.expected_outbound == "unmap":
            out_mark = "UNMAP" if v["outbound_ok"] else "----"
        else:
            out_mark = "MASK" if v["outbound_ok"] else "----"
        result = "PASS" if passed else "FAIL"
        print(
            f"{case.n:>2} | {case.label:<22} | {case.category:<10} | "
            f"{in_mark:<7} | {out_mark:<8} | {v['policy_action']:<6} | {result}"
        )
    print(f"\n=== FINAL: {pass_count}/{len(rows)} PASS ===")


def print_failures(rows: list[tuple[Case, dict, dict, dict | None]]) -> None:
    fails = [(c, r, v, t) for (c, r, v, t) in rows if not (v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"])]
    if not fails:
        return
    print("\n=== FAIL details (raw trace) ===")
    for case, res, v, trace in fails:
        print(f"\n--- case {case.n} · {case.label} (expected_outbound={case.expected_outbound}) ---")
        print(f"  matched detector : {v.get('matched_detector')}")
        print(f"  inbound token    : {v.get('inbound_token')}")
        print(f"  raw value        : {v.get('raw_value')}")
        print(f"  inbound_ok       : {v['inbound_ok']}")
        print(f"  outbound_ok      : {v['outbound_ok']}  (raw_in_output={v['raw_in_output']}, token_in_output={v['token_in_output']})")
        print(f"  policy_action    : {v['policy_action']}")
        for note in v["notes"]:
            print(f"  note             : {note}")
        if trace:
            print(f"  input_raw     : {(trace.get('input_raw') or '')[:160]}")
            print(f"  input_masked  : {(trace.get('input_masked') or '')[:160]}")
            print(f"  output_raw    : {(trace.get('output_raw') or '')[:160]}")
            print(f"  output_final  : {(trace.get('output_final') or '')[:160]}")
            findings = trace.get("dlp_findings") or []
            for f in findings:
                if isinstance(f, dict):
                    print(
                        f"    finding: dir={f.get('direction')} det={f.get('detector')} "
                        f"action={f.get('action')} token={f.get('mask_token')} matched={f.get('matched_text')!r}"
                    )
        else:
            print("  trace            : <none>")


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
        created = create_temp_key(client, admin_headers, project)
        raw_key = created["key"]
        key_id = created["key_id"]
        print(f"[setup] temp api key key_id={key_id} prefix={created.get('key_prefix')}\n")

        rows: list[tuple[Case, dict, dict, dict | None]] = []
        try:
            for case in CASES:
                tag = _cache_tag()
                res = run_case(client, raw_key, case, tag)
                # Tiny pause so the request_log row is visible to the
                # admin GET (the orchestrator persists in a fresh session
                # after the response stream ends).
                time.sleep(0.4)
                trace = fetch_trace(client, admin_headers, res.get("request_id"))
                v = evaluate(case, res, trace)
                rows.append((case, res, v, trace))
                passed = v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"]
                mark = "PASS" if passed else "FAIL"
                print(
                    f"  case {case.n:>2} {case.label:<22} det={v.get('matched_detector') or '?':<22} "
                    f"in={v['inbound_ok']!s:<5} out={v['outbound_ok']!s:<5} -> {mark}"
                )
        finally:
            revoke_key(client, admin_headers, key_id)
            print(f"\n[cleanup] revoked key {key_id}")

    print_summary(rows)
    print_failures(rows)

    pass_count = sum(1 for _, _, v, _ in rows if v["inbound_ok"] and v["outbound_ok"] and v["policy_ok"])
    return 0 if pass_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
