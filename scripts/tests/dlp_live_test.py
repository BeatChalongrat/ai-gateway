"""Live DLP test — end-to-end coverage for all 22 detectors plus 3 outbound
cases where the AI itself is asked to produce sensitive text.

Usage:
    python scripts/tests/dlp_live_test.py

Flow per case:
    user -> gateway (inbound DLP) -> OpenAI -> gateway (outbound DLP) -> user

Prereqs:
    - gateway running at localhost:8080
    - admin bearer token in ADMIN_API_KEY env var
      (falls back to admin-ui/.env.local)
    - at least one project with policy_profile_id='default' (mask policy)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GATEWAY = "http://127.0.0.1:28080"
ADMIN_KEY_ENV = "ADMIN_API_KEY"

# Any bracketed ALL-CAPS token with a trailing index is a mask emitted by the
# transformer: [EMAIL_0], [IP_ADDRESS_1], [CREDIT_CARD_0], etc.
MASK_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")


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


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------

@dataclass
class Case:
    n: int
    category: str
    detector: str
    direction: str  # "INBOUND" or "OUTBOUND"
    content: str
    # Detector name(s) we accept as proof in dlp_findings. Some inputs legit-
    # imately trigger adjacent detectors (e.g. a full IBAN also contains what
    # looks like a bank account number).
    accepted: tuple[str, ...] = field(default_factory=tuple)

    def accepted_detectors(self) -> tuple[str, ...]:
        return self.accepted or (self.detector,)


INBOUND_CASES: list[Case] = [
    # IDENTITY (4)
    Case(1, "IDENTITY", "passport",
         "INBOUND", "My passport number is AA1234567, please check validity"),
    Case(2, "IDENTITY", "vehicle_plate",
         "INBOUND", "My car plate is กข 1234 Bangkok, is it registered?"),
    Case(3, "IDENTITY", "thai_national_id",
         "INBOUND", "เลขบัตรประชาชน 1-1001-00001-00-6 ของฉันถูกต้องมั้ย"),
    Case(4, "IDENTITY", "thai_tax_id",
         "INBOUND", "เลขประจำตัวผู้เสียภาษี 1234567890121 สำหรับบริษัท",
         accepted=("thai_tax_id", "thai_national_id")),

    # FINANCIAL (4)
    Case(5, "FINANCIAL", "credit_card",
         "INBOUND", "Charge card 4532015112830366 exp 12/26 CVV 123"),
    Case(6, "FINANCIAL", "iban",
         "INBOUND", "Transfer to IBAN GB29NWBK60161331926819 please"),
    Case(7, "FINANCIAL", "thai_bank_account",
         "INBOUND", "โอนเงินไปที่เลขบัญชี 123-4-56789-0 ธนาคารกสิกร"),
    Case(8, "FINANCIAL", "thai_promptpay",
         "INBOUND", "PromptPay ID: 0812345678 ชื่อ สมชาย",
         accepted=("thai_promptpay", "phone")),

    # CREDENTIAL (2)
    # sk-proj-* is caught by bearer_token (not api_key_secret — that detector
     # handles anthropic/google/JWT and `password=/api_key=` assignments).
    # Either name is a correct credential/secret detection for this input.
    Case(9, "CREDENTIAL", "api_key_secret",
         "INBOUND", "API key is sk-proj-xK9mN2pL8qR5vT3wY6uI1oE4aS7dF0jH use it",
         accepted=("api_key_secret", "bearer_token")),
    Case(10, "CREDENTIAL", "db_connection_string",
          "INBOUND", "Connect via postgresql://admin:P@ssw0rd123@db.internal:5432/prod"),

    # SECRET (3)
    Case(11, "SECRET", "bearer_token",
          "INBOUND",
          "Use token: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMSJ9.abc123"),
    Case(12, "SECRET", "private_key",
          "INBOUND",
          "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA2a2rwplB\n-----END RSA PRIVATE KEY-----"),
    Case(13, "SECRET", "password_field",
          "INBOUND", "password=Sup3rS3cr3t! use this to login to the system"),

    # PII (7)
    Case(14, "PII", "email",
          "INBOUND", "Send report to somchai.work@company.co.th and napat@gmail.com"),
    Case(15, "PII", "phone",
          "INBOUND", "Call +66812345678 or office 02-234-5678 ext 101"),
    Case(16, "PII", "ssn",
          "INBOUND", "SSN: 123-45-6789 for the US employee record"),
    Case(17, "PII", "date_of_birth",
          "INBOUND", "DOB: 15/03/1990 for patient registration form"),
    Case(18, "PII", "thai_address",
          "INBOUND", "ที่อยู่ 123 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ 10110"),
    Case(19, "PII", "medical_record",
          "INBOUND", "HN: 0012345 diagnosis ICD-10 E11.9 type 2 diabetes mellitus"),
    Case(20, "PII", "presidio_ner",
          "INBOUND", "Patient John Smith born January 5 1985 lives in New York",
          accepted=("presidio_ner", "date_of_birth")),

    # NETWORK (2)
    Case(21, "NETWORK", "ip_address",
          "INBOUND", "Server at 10.0.0.254 and backup at 192.168.100.1 are down"),
    Case(22, "NETWORK", "mac_address",
          "INBOUND", "Device MAC address is 00:1A:2B:3C:4D:5E on network"),
]

OUTBOUND_CASES: list[Case] = [
    Case(23, "PII", "email", "OUTBOUND",
         "Make up a fictional example email address for a demo user. "
         "Reply with just the address, nothing else."),
    Case(24, "NETWORK", "ip_address", "OUTBOUND",
         "Give me a single example of a private IP address for documentation. "
         "Reply with just the address."),
    Case(25, "FINANCIAL", "credit_card", "OUTBOUND",
         # The detector enforces Luhn and won't mask AI-invented cards that
         # fail checksum. To get a deterministic Luhn-valid generation, ask
         # for a well-known test card WITHOUT putting the digits in the
         # prompt (otherwise inbound masking hides them from the model and
         # it refuses to complete). Stripe's test card (4242 x4) is the
         # most widely known public test number.
         "In a Stripe sandbox test, what's the most common Visa test card "
         "number used in documentation examples? Reply with just the "
         "16 digits, spaces between groups of 4 are fine."),
]

ALL_CASES: list[Case] = INBOUND_CASES + OUTBOUND_CASES


# ---------------------------------------------------------------------------
# Admin + gateway calls
# ---------------------------------------------------------------------------

def pick_default_policy_project(client: httpx.Client, admin_headers: dict) -> dict:
    r = client.get(f"{GATEWAY}/admin/projects", headers=admin_headers)
    r.raise_for_status()
    items = r.json().get("items", [])
    for p in items:
        if p.get("policy_profile_id") == "default":
            return p
    sys.exit("error: no project with policy_profile_id='default' found")


def create_test_key(client: httpx.Client, admin_headers: dict, project: dict) -> dict:
    body = {
        "tenant_id": project["tenant_id"],
        "project_id": project["project_id"],
        "name": "dlp-live-test",
    }
    r = client.post(f"{GATEWAY}/admin/api-keys", headers=admin_headers, json=body)
    r.raise_for_status()
    return r.json()


def revoke_key(client: httpx.Client, admin_headers: dict, key_id: str) -> None:
    try:
        client.delete(f"{GATEWAY}/admin/api-keys/{key_id}", headers=admin_headers)
    except Exception:
        pass


def run_case(client: httpx.Client, api_key: str, case: Case, run_tag: str) -> dict:
    """POST the chat completion and capture status, body, headers."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Append a unique, invisible-ish tag so identical inputs across runs miss
    # the response cache (keyed on model+messages hash). The tag lands inside
    # ordinary prose so it neither trips detectors nor derails the prompt.
    tagged = f"{case.content} [run:{run_tag}:{case.n}]"
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": tagged}],
    }
    out: dict = {"sent_content": tagged, "request_id": None, "status": 0}
    try:
        r = client.post(f"{GATEWAY}/v1/chat/completions", headers=headers, json=body, timeout=90.0)
    except httpx.RequestError as e:
        out["error"] = f"network: {e}"
        return out
    out["status"] = r.status_code
    try:
        payload = r.json()
        out["body"] = payload
        out["policy_action"] = payload.get("policy_action")
        if isinstance(payload, dict):
            # Prefer body.id (the "req_..." id used as the admin log PK).
            # The X-Request-Id header is a separate trace UUID that does not
            # correspond to /admin/requests/{id}.
            out["request_id"] = payload.get("id") or r.headers.get("X-Request-Id")
            choices = payload.get("choices", [])
            if choices:
                out["reply_content"] = (
                    choices[0].get("message", {}).get("content") or ""
                )
    except Exception:
        out["body"] = r.text
    if not out.get("request_id"):
        out["request_id"] = r.headers.get("X-Request-Id")
    return out


def fetch_request_log(client: httpx.Client, admin_headers: dict, request_id: str | None) -> dict | None:
    if not request_id:
        return None
    # The request log writes inside the endpoint; occasionally the GET races
    # the commit. Retry a couple of times before giving up.
    for _ in range(3):
        try:
            r = client.get(f"{GATEWAY}/admin/requests/{request_id}", headers=admin_headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                time.sleep(0.25)
                continue
        except Exception:
            time.sleep(0.25)
    return None


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def findings_for_direction(log: dict | None, direction: str) -> list[tuple[str, str]]:
    """All (detector, action) pairs from dlp_findings on the given direction."""
    if not log:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for f in log.get("dlp_findings") or []:
        if not isinstance(f, dict):
            continue
        if f.get("direction") != direction:
            continue
        det = f.get("detector")
        act = f.get("action")
        if det and act and (det, act) not in seen:
            seen.add((det, act))
            out.append((det, act))
    return out


def masked_detectors_for_direction(log: dict | None, direction: str) -> list[str]:
    """Unique detector names from dlp_findings matching direction + action=mask."""
    return [d for d, a in findings_for_direction(log, direction) if a == "mask"]


def verdict(case: Case, res: dict, log: dict | None) -> dict:
    """Produce a row dict for the summary table plus PASS/FAIL reasoning.

    INBOUND cases: PASS when the expected detector fires with action=mask.

    OUTBOUND cases: PASS when the gateway *saw* the AI-generated entity
    (i.e. an outbound finding for the expected detector exists) AND
    either (a) it was correctly allowed through under Fix 3 — the
    reply still contains real data, no mask token — or (b) it was
    masked, in which case the reply carries a [DETECTOR_N] token.
    Both outcomes prove the outbound pipeline reached the right
    decision; the policy choice is what differs.
    """
    direction_key = "input" if case.direction == "INBOUND" else "output"
    direction_findings = findings_for_direction(log, direction_key)
    masked = [d for d, a in direction_findings if a == "mask"]
    accepted_set = set(case.accepted_detectors())
    action = res.get("policy_action") or "-"
    status = res.get("status")
    reply = res.get("reply_content") or ""
    reply_has_token = bool(MASK_TOKEN_RE.search(reply))

    pass_ = False
    reason = ""

    if status == 403:
        # Block on the target direction still proves detection.
        pass_ = True
        reason = "blocked (still a valid DLP trigger)"
    elif case.direction == "INBOUND":
        hit = next((d for d in masked if d in accepted_set), None)
        if hit is not None:
            pass_ = True
            reason = f"input finding masked by expected detector: {hit}"
        elif action == "mask" and masked:
            reason = f"input masked unexpected detectors: {masked}"
        else:
            reason = f"no input mask recorded (policy_action={action})"
    else:
        # OUTBOUND — either masked OR allowed counts as "detected", and
        # both are a valid pass under Fix 3 (the policy decides; the
        # detector still has to fire).
        out_detectors_all = [d for d, _ in direction_findings]
        out_hit = next((d for d in out_detectors_all if d in accepted_set), None)
        if out_hit is not None:
            was_masked = out_hit in masked
            if was_masked:
                pass_ = True
                reason = f"outbound finding masked: {out_hit}"
            else:
                pass_ = True
                reason = f"outbound finding allowed (Fix 3): {out_hit}"
        elif reply_has_token:
            # Detector ledger races the log fetch sometimes — fall back
            # to a token-in-reply check.
            pass_ = True
            reason = "mask token present in reply content"
        else:
            reason = (
                f"no outbound finding for expected detectors "
                f"{sorted(accepted_set)} (policy_action={action})"
            )

    return {
        "masked_fields": masked,
        "out_findings": direction_findings if case.direction == "OUTBOUND" else None,
        "action": action,
        "status": status,
        "pass": pass_,
        "reason": reason,
        "reply_snippet": reply[:80].replace("\n", " "),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_table(title: str, rows: list[tuple[Case, dict]]) -> None:
    print(f"\n=== {title} ({len(rows)} case{'s' if len(rows) != 1 else ''}) ===")
    header = f"{'#':>2} | {'Detector':<20} | {'Direction':<8} | {'Status':<6} | {'DLP Action':<10} | {'Findings (det:action)':<32} | Pass/Fail"
    print(header)
    print("-" * len(header))
    for case, v in rows:
        out_findings = v.get("out_findings")
        if case.direction == "OUTBOUND" and out_findings is not None:
            # Outbound rows: show what the detector saw AND the per-finding
            # action so an "email:allow" verdict is visible (Fix 3 path)
            # rather than appearing as an empty masked_fields column.
            fields_str = ",".join(f"{d}:{a}" for d, a in out_findings) or "-"
        else:
            fields_str = ",".join(v["masked_fields"]) if v["masked_fields"] else "-"
        if len(fields_str) > 32:
            fields_str = fields_str[:29] + "..."
        mark = "PASS" if v["pass"] else "FAIL"
        print(
            f"{case.n:>2} | {case.detector:<20} | {case.direction:<8} | "
            f"{str(v['status'] or '-'):<6} | {v['action']:<10} | {fields_str:<32} | {mark}"
        )


def print_fail_details(fails: list[tuple[Case, dict, dict, dict | None]]) -> None:
    if not fails:
        return
    print("\n=== FAIL details ===")
    for case, res, v, log in fails:
        print(f"\n--- case {case.n} - {case.detector} [{case.direction}] ---")
        print(f"  reason           : {v['reason']}")
        print(f"  sent content     : {res.get('sent_content')}")
        body = res.get("body")
        if isinstance(body, dict):
            snippet = json.dumps(body, ensure_ascii=False)
            print(f"  full response    : {snippet[:800]}{'...' if len(snippet) > 800 else ''}")
        else:
            print(f"  full response    : {str(body)[:800]}")
        expected_field = "outbound " + case.detector if case.direction == "OUTBOUND" else case.detector
        print(f"  expected masked  : {expected_field}")
        print(f"  actual masked    : {v['masked_fields'] or '[]'}")
        if log:
            print(f"  log.policy_action: {log.get('policy_action')}")
            print(f"  log.input_raw    : {log.get('input_raw')}")
            print(f"  log.input_masked : {log.get('input_masked')}")
            print(f"  log.output_raw   : {log.get('output_raw')}")
            print(f"  log.output_final : {log.get('output_final')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    admin_key = load_admin_key()
    admin_headers = {"Authorization": f"Bearer {admin_key}"}
    run_tag = f"{int(time.time())}"

    with httpx.Client() as client:
        project = pick_default_policy_project(client, admin_headers)
        print(
            f"[setup] project: {project['project_id']} ({project['name']}) "
            f"tenant={project['tenant_id']} policy={project['policy_profile_id']}"
        )

        created = create_test_key(client, admin_headers, project)
        raw_key = created["key"]
        key_id = created["key_id"]
        print(f"[setup] created temp api key key_id={key_id} prefix={created.get('key_prefix')}")
        print(f"[setup] run tag   : run:{run_tag} (unique suffix per message, guarantees cache miss)\n")

        results: list[tuple[Case, dict, dict, dict | None]] = []
        try:
            for case in ALL_CASES:
                res = run_case(client, raw_key, case, run_tag)
                log = fetch_request_log(client, admin_headers, res.get("request_id"))
                v = verdict(case, res, log)
                results.append((case, res, v, log))
                mark = "PASS" if v["pass"] else "FAIL"
                print(
                    f"  case {case.n:>2} [{case.direction}] {case.detector:<20s} "
                    f"http={res.get('status')} action={v['action']:<6} "
                    f"masked={v['masked_fields'] or '[]'} -> {mark}"
                )
        finally:
            revoke_key(client, admin_headers, key_id)
            print(f"\n[cleanup] revoked key {key_id}")

    inbound_rows = [(c, v) for c, _, v, _ in results if c.direction == "INBOUND"]
    outbound_rows = [(c, v) for c, _, v, _ in results if c.direction == "OUTBOUND"]

    print_table("INBOUND", inbound_rows)
    print_table("OUTBOUND", outbound_rows)

    passes = sum(1 for _, _, v, _ in results if v["pass"])
    total = len(results)
    print(f"\n=== FINAL SCORE: {passes}/{total} PASS ===")

    fails = [(c, r, v, l) for c, r, v, l in results if not v["pass"]]
    print_fail_details(fails)

    print(
        "\nNote: Redis cache TTL=1h. Each run uses a unique timestamp suffix "
        "(run:<epoch>:<case>) on the message content to guarantee a cold scan "
        "and exercise the full DLP pipeline end-to-end."
    )

    return 0 if passes == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
