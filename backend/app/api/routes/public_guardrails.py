"""FIX22 — two small read-only endpoints for the desktop client.

POST /v1/guardrails/check
    Write-time guardrail check (FIX22 §1a). A client that is about to STORE
    text which will later land in an assembled prompt (a `remember` note, a
    server-side memory, project instructions, a writing-style profile) asks
    the gateway *first* whether that text would trip the content guardrail.
    A saved note that matches a rule 403s every future turn, silently and
    permanently; refusing it at write time cuts the injection chain at the
    point of storage.

    This route CALLS ``GuardrailService.check`` — the very same code the
    chat path runs (orchestrator.py / public_chat.py) — so there is exactly
    one set of rules and one matcher. It does not modify the service, does
    not add a trust flag, and does not drop a role from the scan (that
    design question is deliberately parked; see docs/FIX21_REPORT.md §1).
    The matched span is located with the same compiled patterns so the
    caller can tell the user WHICH phrase is the problem.

    Diagnostic only: nothing here is an enforcement point. The chat path
    keeps enforcing on every turn regardless of what a client did with this
    answer.

GET /v1/desktop/version
    Update *notification* stage 1 (FIX22 §2): the current desktop version,
    a bare string set by the operator (``DESKTOP_LATEST_VERSION``). No file,
    no URL, no feed — the app compares it with its own version and shows a
    dismissible banner; the user still installs by hand. Unset → 404, and
    the app shows nothing (an unconfigured deployment looks exactly like
    today).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.services.guardrail_service import _COMPILED, GuardrailService

router = APIRouter()

# Generous: a writing-style profile is capped at 8000 chars by the sidecar,
# project instructions at 8000 by chat-edge; a note is one sentence. Anything
# larger than this is not a prompt fragment.
MAX_CHECK_CHARS = 20_000


class GuardrailCheckRequest(BaseModel):
    text: str = Field(..., max_length=MAX_CHECK_CHARS)


class GuardrailCheckResponse(BaseModel):
    passed: bool
    rule_name: str | None = None
    reason: str | None = None
    # The exact span the rule matched (first match), so the client can show
    # the user which phrase to reword. Never re-sent by the client — shown.
    matched: str | None = None


_guardrail = GuardrailService()


def _first_match(rule_name: str, text: str) -> str | None:
    """Locate the span behind ``rule_name`` with the SAME compiled patterns
    the service scans with (read-only use of the service's own table)."""
    for name, pattern in _COMPILED:
        if name != rule_name:
            continue
        m = pattern.search(text)
        if m:
            return m.group(0)[:200]
    return None


@router.post("/guardrails/check", response_model=GuardrailCheckResponse)
async def guardrail_check(
    body: GuardrailCheckRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
) -> GuardrailCheckResponse:
    """Would this text be blocked by the content guardrail if it were part
    of a request? Runs the chat path's own checker over it as a single
    user-role message (the scanner joins user+system text, so the role is
    immaterial to the verdict)."""
    del auth  # authenticated like every /v1 route; identity is not used
    result = _guardrail.check([{"role": "user", "content": body.text}])
    if result.passed:
        return GuardrailCheckResponse(passed=True)
    return GuardrailCheckResponse(
        passed=False,
        rule_name=result.rule_name,
        reason=result.reason,
        matched=_first_match(result.rule_name or "", body.text),
    )


class DesktopVersionResponse(BaseModel):
    version: str


@router.get("/desktop/version", response_model=DesktopVersionResponse)
async def desktop_version(
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
) -> DesktopVersionResponse:
    """The current SRAN Desktop version the operator wants installed.
    404 when ``DESKTOP_LATEST_VERSION`` is unset — the client treats any
    non-200 as "say nothing"."""
    del auth
    version = (settings.desktop_latest_version or "").strip()
    if not version:
        raise NotFoundError("No desktop version is published on this gateway.")
    return DesktopVersionResponse(version=version)
