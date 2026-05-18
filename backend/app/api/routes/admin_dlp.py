"""Admin routes for Stage 7 DLP UI: detectors, policy profiles, NER settings.

These endpoints are mounted under the main admin router (``/admin/*``) by
``admin.py``. Keeping them in a separate module avoids bloating the main
admin route file further and isolates DLP concerns.

Auth: all routes inherit the ``require_admin`` dependency from the admin
router, so no additional auth plumbing is needed here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.org_settings import OrgSettingsORM
from app.services.dlp import ner_status
from app.services.dlp.detectors.registry import get_default_registry
from app.services.policy import profile_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Detector list
# ---------------------------------------------------------------------------


@router.get("/dlp/detectors")
def list_detectors() -> dict[str, Any]:
    """Return the registered detector set with category + severity.

    Used by the admin UI detector list to render category pills and the
    default-action select. The admin UI looks up the current action for
    each detector via the active profile (see /dlp/policy-profiles).
    """
    # Build a full catalog (disabled_detectors=[]) so the admin UI can
    # render every detector and show its enabled/disabled state based on
    # the current configuration. A detector disabled via
    # ``settings.dlp_disabled_detectors`` is excluded from the runtime
    # pipeline but must still appear here for visibility.
    registry = get_default_registry(disabled_detectors=[])
    disabled_set = set(settings.dlp_disabled_detectors_list)
    items: list[dict[str, Any]] = []
    for d in registry.all():
        items.append(
            {
                "name": d.name,
                "category": d.category,
                "severity": _severity_name(d.default_sensitivity),
                "enabled": d.name not in disabled_set,
                "is_ner": d.category == "ner",
            }
        )
    return {"items": items}


def _severity_name(value: Any) -> str:
    try:
        return value.name.lower()
    except AttributeError:
        return str(value).lower()


# ---------------------------------------------------------------------------
# Policy profiles
# ---------------------------------------------------------------------------


class CategoryActionPatch(BaseModel):
    category: str = Field(..., min_length=1, max_length=50)
    action: str = Field(..., min_length=1, max_length=20)


@router.get("/policy-profiles")
def list_policy_profiles(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    views = profile_service.list_profiles(session)
    return {
        "items": [
            {
                "id": v.profile_id,
                "profile_id": v.profile_id,
                "name": v.name,
                "category_actions": v.category_actions,
                "category_actions_output": v.category_actions_output,
                "severity_threshold_block": v.severity_threshold_block,
            }
            for v in views
        ]
    }


@router.patch("/policy-profiles/{profile_id}/category-actions")
def patch_policy_profile_category_action(
    profile_id: str,
    body: CategoryActionPatch,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        view = profile_service.set_category_action(
            session,
            profile_id=profile_id,
            category=body.category,
            action=body.action,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("Unknown profile"):
            raise NotFoundError(msg) from exc
        raise ValidationError(msg) from exc

    return {
        "id": view.profile_id,
        "profile_id": view.profile_id,
        "name": view.name,
        "category_actions": view.category_actions,
        "severity_threshold_block": view.severity_threshold_block,
    }


# ---------------------------------------------------------------------------
# NER status + org settings
# ---------------------------------------------------------------------------


def _get_or_create_settings(session: Session) -> OrgSettingsORM:
    row = session.get(OrgSettingsORM, 1)
    if row is None:
        row = OrgSettingsORM(id=1, ner_enabled=False)
        session.add(row)
        session.flush()
    return row


@router.get("/ner/status")
def get_ner_status(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = _get_or_create_settings(session)
    if not row.ner_enabled:
        return {
            "status": "not_loaded",
            "message": "NER disabled in settings",
            "ner_enabled": False,
        }

    current = ner_status.get_status()
    payload: dict[str, Any] = {"status": current.status, "ner_enabled": True}
    if current.message is not None:
        payload["message"] = current.message
    return payload


class SettingsPatch(BaseModel):
    ner_enabled: bool | None = None


@router.patch("/settings")
def patch_settings(
    body: SettingsPatch,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    row = _get_or_create_settings(session)

    if body.ner_enabled is not None:
        previous = row.ner_enabled
        row.ner_enabled = body.ner_enabled
        session.flush()

        if body.ner_enabled and not previous:
            # Transition off→on: mark loading and schedule the loader.
            ner_status.begin_load()
            background_tasks.add_task(ner_status.load_analyzer_sync)
        elif not body.ner_enabled and previous:
            ner_status.reset()

    return {"ner_enabled": row.ner_enabled}


# ---------------------------------------------------------------------------
# Coverage monitor + quick test
# ---------------------------------------------------------------------------


# Human-curated per-detector metadata for the admin UI coverage page.
# Keyed on ``detector.name``. Anything not listed falls back to a
# generic description + empty example list at response build time.
_DETECTOR_META: dict[str, dict[str, Any]] = {
    "email": {
        "method": "regex",
        "mask_token": "[EMAIL]",
        "coverage_note": "RFC 5322-ish email addresses",
        "example_patterns": ["user@example.com"],
    },
    "phone": {
        "method": "regex",
        "mask_token": "[PHONE_NUMBER]",
        "coverage_note": "International + grouped phone numbers (10-15 digits)",
        "example_patterns": ["+66 81 234 5678", "555-123-4567"],
    },
    "ssn": {
        "method": "regex",
        "mask_token": "[SSN]",
        "coverage_note": "US Social Security number",
        "example_patterns": ["123-45-6789"],
    },
    "credit_card": {
        "method": "checksum",
        "mask_token": "[CREDIT_CARD]",
        "coverage_note": "Luhn-validated 13-19 digit card numbers",
        "example_patterns": ["4111-1111-1111-1111"],
    },
    "iban": {
        "method": "checksum",
        "mask_token": "[IBAN]",
        "coverage_note": "International Bank Account Number (mod-97)",
        "example_patterns": ["GB82 WEST 1234 5698 7654 32"],
    },
    "ip_address": {
        "method": "regex",
        "mask_token": "[IP_ADDRESS]",
        "coverage_note": "IPv4 + common IPv6 forms",
        "example_patterns": ["192.168.1.1", "::1"],
    },
    "passport": {
        "method": "regex",
        "mask_token": "[PASSPORT]",
        "coverage_note": "Context-gated multi-country passport",
        "example_patterns": ["passport no AB1234567"],
    },
    "date_of_birth": {
        "method": "regex",
        "mask_token": "[DATE_OF_BIRTH]",
        "coverage_note": "Date formats near DOB keywords",
        "example_patterns": ["DOB: 1990-01-15"],
    },
    "vehicle_plate": {
        "method": "regex",
        "mask_token": "[VEHICLE_PLATE]",
        "coverage_note": "TH / US license plate shapes",
        "example_patterns": [],
    },
    "bearer_token": {
        "method": "regex",
        "mask_token": "[BEARER_TOKEN]",
        "coverage_note": "OpenAI sk-, AWS AKIA, GitHub ghp_, Slack xox*",
        "example_patterns": ["AKIAIOSFODNN7EXAMPLE"],
    },
    "api_key_secret": {
        "method": "regex",
        "mask_token": "[CREDENTIAL]",
        "coverage_note": "Anthropic sk-ant-, Google AIza, JWT, key=value assignments",
        "example_patterns": ["sk-ant-api03-…", "password=xyz"],
    },
    "private_key": {
        "method": "regex",
        "mask_token": "[PRIVATE_KEY]",
        "coverage_note": "PEM BEGIN PRIVATE KEY headers",
        "example_patterns": ["-----BEGIN PRIVATE KEY-----"],
    },
    "password_field": {
        "method": "regex",
        "mask_token": "[PASSWORD]",
        "coverage_note": "password=/passwd: field assignments",
        "example_patterns": ["password=hunter2"],
    },
    "thai_national_id": {
        "method": "checksum",
        "mask_token": "[THAI_ID]",
        "coverage_note": "Thai 13-digit national ID with mod-11 checksum",
        "example_patterns": ["1-1017-00230-70-8"],
    },
    "thai_tax_id": {
        "method": "checksum",
        "mask_token": "[TAX_ID]",
        "coverage_note": "Thai tax ID — same shape as national ID, keyword-gated",
        "example_patterns": ["tax id: 5011000234569"],
    },
    "thai_bank_account": {
        "method": "regex",
        "mask_token": "[BANK_ACCOUNT]",
        "coverage_note": "10-12 digit account numbers, keyword-gated",
        "example_patterns": ["บัญชี 123-4-56789-0"],
    },
    "thai_promptpay": {
        "method": "regex",
        "mask_token": "[PROMPTPAY]",
        "coverage_note": "PromptPay mobile / national-ID identifiers",
        "example_patterns": ["พร้อมเพย์ 0812345678"],
    },
    "thai_address": {
        "method": "regex",
        "mask_token": "[ADDRESS]",
        "coverage_note": "Thai postal addresses anchored on บ้านเลขที่ / ที่อยู่",
        "example_patterns": [],
    },
    "address": {
        "method": "regex",
        "mask_token": "[ADDRESS]",
        "coverage_note": "Labeled English/Thai address fields",
        "example_patterns": ["Address: 99/88 Sukhumvit Road, Bangkok 10110"],
    },
    "income": {
        "method": "regex",
        "mask_token": "[INCOME]",
        "coverage_note": "Labeled salary / monthly income fields",
        "example_patterns": ["Salary: 85,000 THB/month"],
    },
    "thai_name": {
        "method": "regex",
        "mask_token": "[THAI_NAME]",
        "coverage_note": "Thai personal names with title prefix (นาย/นาง/นางสาว/คุณ/ดร./etc.)",
        "example_patterns": ["นายสมชาย ใจดี", "นางสาวพิมพ์ลดา จันทร์สว่าง"],
    },
    "db_connection_string": {
        "method": "regex",
        "mask_token": "[CREDENTIAL]",
        "coverage_note": "postgres/mysql/mongodb/redis/mssql URIs with embedded credentials",
        "example_patterns": ["postgresql://user:pass@host/db"],
    },
    "medical_record": {
        "method": "regex",
        "mask_token": "[MEDICAL_RECORD]",
        "coverage_note": "6-10 digit MRN / HN / patient ID, keyword-gated",
        "example_patterns": ["HN 1234567"],
    },
    "mac_address": {
        "method": "regex",
        "mask_token": "[MAC_ADDRESS]",
        "coverage_note": "6-group colon/hyphen hex MAC, rejects 00/FF broadcast",
        "example_patterns": ["aa:bb:cc:dd:ee:ff"],
    },
    "presidio_ner": {
        "method": "ner",
        "mask_token": "[NAME]",
        "coverage_note": "Presidio NER — PERSON / LOCATION / ORG / GPE / NRP",
        "example_patterns": [],
    },
    "line_id": {
        "method": "regex",
        "mask_token": "[LINE_ID]",
        "coverage_note": "LINE messaging-app handle, keyword-gated to avoid "
                         "Twitter/Slack mention false positives",
        "example_patterns": ["LINE: @somchai_dev"],
    },
}


# Hand-curated list of known coverage gaps. Not DB-driven — these are
# editorial notes for developers and contributors, displayed in the
# admin UI coverage page so the "what's not covered yet" story is
# explicit rather than implicit.
_KNOWN_GAPS: list[dict[str, str]] = [
    {
        "category": "medical",
        "description": "Medical diagnosis, condition, or drug names",
        "reason": "Requires a medical NER model (general-purpose Presidio "
                  "doesn't distinguish 'diabetes' from 'a common word').",
        "priority": "medium",
    },
    {
        "category": "financial",
        "description": "Salary, account balance, or compensation figures",
        "reason": "High false-positive rate without domain-specific context "
                  "(every invoice would trigger).",
        "priority": "low",
    },
    {
        "category": "identity",
        "description": "Biometric identifiers (retina / fingerprint hashes)",
        "reason": "No standard textual representation — would need a format "
                  "spec and a source to test against.",
        "priority": "low",
    },
    {
        "category": "pii",
        "description": "Non-Thai / non-English national IDs (EU, ASEAN, LATAM)",
        "reason": "Each jurisdiction has its own shape and checksum; "
                  "covering all of them is a linear amount of work that "
                  "hasn't been prioritised yet.",
        "priority": "medium",
    },
    {
        "category": "credential",
        "description": "OAuth client secrets / refresh tokens",
        "reason": "No distinctive format — would need context-keyword gating "
                  "similar to the api_key_secret assignment detector.",
        "priority": "medium",
    },
]


def _severity_name_from_enum(value: Any) -> str:
    try:
        return value.name.upper()
    except AttributeError:
        return str(value).upper()


@router.get("/dlp/coverage")
def get_dlp_coverage(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return detector metadata grouped by category + known gaps.

    Used by the admin UI coverage page to render the per-category
    cards and the gap list. Admin-only (inherits from the admin router).
    """
    # Build a full catalog (disabled_detectors=[]) so the UI can show
    # every detector, then reflect the actual enabled/disabled state
    # from settings on each entry. This keeps the catalog view complete
    # while the scan pipeline still respects the disabled list.
    registry = get_default_registry(disabled_detectors=[])
    disabled_set = set(settings.dlp_disabled_detectors_list)
    detectors_out: list[dict[str, Any]] = []
    by_category: dict[str, dict[str, Any]] = {}

    for det in registry.all():
        meta = _DETECTOR_META.get(det.name, {})
        entry = {
            "name": det.name,
            "category": det.category,
            "severity": _severity_name_from_enum(det.default_sensitivity),
            "mask_token": meta.get("mask_token", "[REDACTED_PII]"),
            "enabled": det.name not in disabled_set,
            "method": meta.get("method", "regex"),
            "coverage_note": meta.get("coverage_note", ""),
            "example_patterns": meta.get("example_patterns", []),
            "gaps": [],
        }
        detectors_out.append(entry)
        cat = by_category.setdefault(
            det.category, {"count": 0, "detectors": []}
        )
        cat["count"] += 1
        cat["detectors"].append(det.name)

    return {
        "summary": {
            "total": len(detectors_out),
            "by_category": by_category,
        },
        "detectors": detectors_out,
        "known_gaps": _KNOWN_GAPS,
    }


class DlpTestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/dlp/test")
def test_dlp(body: DlpTestRequest) -> dict[str, Any]:
    """Run the full DLP classifier over *text* and return findings + masked text.

    Dev / admin sandbox — runs the real detector registry and
    transformer but does NOT persist anything or call a provider.
    The returned ``masked_text`` keeps indexed tokens (e.g. ``[EMAIL_0]``)
    so the reviewer can see which span became which token.
    """
    from app.domain.dlp import EnforcementAction, MatchAction
    from app.services.dlp.transformer import DLPTransformer

    registry = get_default_registry(disabled_detectors=[])
    matches = registry.scan_text(body.text)

    # For the sandbox we mask EVERY finding so the reviewer can see what
    # the transformer would do. Production code routes this through
    # policy profiles; here we short-circuit to "mask all" for clarity.
    actions = [
        MatchAction(
            match=m,
            action=EnforcementAction.MASK,
            reason_code="sandbox",
            reason_message="sandbox mask-all",
        )
        for m in matches
    ]
    masked_text, _span_map, _counters = DLPTransformer().mask_text(body.text, actions)

    findings = [
        {
            "detector": m.detector_name,
            "entity_type": m.entity_type,
            "category": m.category,
            "severity": _severity_name_from_enum(m.severity),
            "start": m.start,
            "end": m.end,
            "matched_text": m.matched_text,
        }
        for m in matches
    ]
    return {
        "findings": findings,
        "masked_text": masked_text,
        "detector_count": len(registry.all()),
    }
