"""FIX37 mask-quality eval — shared harness.

Phase 1 tooling ONLY. Nothing in this package touches detector code; it
reads the production masking path and scores it.

The masking path mirrors ``GatewayOrchestrator.handle_chat_completion``:

    registry.scan_messages(...)          # detection
    policy_engine.evaluate_decision(...) # classification + policy
    transformer.mask_messages(...)       # the text the model actually sees

Everything scored here is declared in ``criteria.json`` — that file is
LOCKED at the Phase-1 commit (rule 2 of FIX37).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CRITERIA = json.loads((HERE / "criteria.json").read_text(encoding="utf-8"))

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_MASK_TOKEN = re.compile(r"(?:<<[A-Z][A-Z0-9_]*_[A-Z0-9]+>>|\[[A-Z][A-Z0-9_]*_\d+\]|<r\d+>)")


# ── data ──────────────────────────────────────────────────────────────────

def load_messages() -> list[dict]:
    out: list[dict] = []
    for p in sorted(DATA.glob("messages_*.json")):
        out.extend(json.loads(p.read_text(encoding="utf-8"))["messages"])
    return out


def load_profiles() -> list[dict]:
    return json.loads((DATA / "profiles.json").read_text(encoding="utf-8"))["profiles"]


def validate_corpus(messages: list[dict]) -> list[str]:
    """Every declared value must actually occur in its own message."""
    problems: list[str] = []
    seen: set[str] = set()
    for m in messages:
        if m["id"] in seen:
            problems.append(f"{m['id']}: duplicate id")
        seen.add(m["id"])
        for p in m.get("pii", []):
            if p["value"] not in m["text"]:
                problems.append(f"{m['id']}: pii value {p['value']!r} not present in text")
        for d in m.get("must_not_mask", []):
            if d not in m["text"]:
                problems.append(f"{m['id']}: must_not_mask {d!r} not present in text")
        for q in m.get("questions", []):
            if q["kind"] == "identity" and not q.get("leak_values"):
                problems.append(f"{m['id']}/{q['id']}: identity question without leak_values")
            if q["kind"] == "context" and not q.get("expect_any"):
                problems.append(f"{m['id']}/{q['id']}: context question without expect_any")
    return problems


# ── normalization + presence (criteria.normalization) ─────────────────────

def norm(s: str) -> str:
    return (s or "").translate(_THAI_DIGITS).lower()


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def present(value: str, text: str) -> bool:
    """Is *value* present in *text* under the locked normalization rules?"""
    v, t = norm(value).strip(), norm(text)
    if not v:
        return False
    if v in t:
        return True
    dv = _digits(v)
    min_digits = CRITERIA["normalization"]["numeric_min_digits_for_digit_compare"]
    if len(dv) >= min_digits and dv in _digits(t):
        return True
    return False


# ── masking (the production path) ─────────────────────────────────────────

@dataclass
class MaskResult:
    text: str
    span_map: dict
    findings: list  # (detector_name, matched_text, action)


class Masker:
    """Runs detection → policy → mask exactly as the gateway does."""

    def __init__(self, *, ner_enabled: bool, profile_id: str = "default") -> None:
        from app.services.dlp.classifier import SensitivityClassifier
        from app.services.dlp.detectors.registry import get_default_registry
        from app.services.dlp.policy_engine import DLPPolicyEngine
        from app.services.dlp.transformer import DLPTransformer
        from app.services.policy.evaluator import PolicyEvaluator
        from app.services.policy.profiles import get_profile

        self.ner_enabled = ner_enabled
        self.registry = get_default_registry(ner_enabled=ner_enabled)
        self.engine = DLPPolicyEngine(
            classifier=SensitivityClassifier(), evaluator=PolicyEvaluator()
        )
        self.transformer = DLPTransformer()
        self.profile = get_profile(profile_id)

    def mask(self, text: str) -> MaskResult:
        from app.schemas.common import Message

        msgs = [Message(role="user", content=text)]
        detection = self.registry.scan_messages(
            msgs,
            direction="input",
            scan_system_messages=self.profile.scan_system_messages,
        )
        decision = self.engine.evaluate_decision(
            detection_result=detection, profile=self.profile, text=text
        )
        findings = [
            (ma.match.detector_name, ma.match.matched_text, getattr(ma, "action", None))
            for ma in decision.match_actions
        ]
        if not decision.requires_transformation:
            return MaskResult(text=text, span_map={}, findings=findings)
        masked, span_map, _counters = self.transformer.mask_messages(
            msgs, decision.match_actions
        )
        return MaskResult(text=masked[0].content, span_map=span_map, findings=findings)


def placeholder_tokens(text: str) -> list[str]:
    return _MASK_TOKEN.findall(text or "")


def placeholder_prefix(token: str) -> str:
    body = token.strip("<>[]")
    return body.rsplit("_", 1)[0]


# ── model client (axis A / B) ─────────────────────────────────────────────

class GatewayClient:
    """Chat client for the two axis-A / axis-B arms.

    IMPORTANT (methodology): the arms go **direct to the provider**, not
    through ``/v1/chat/completions``. The gateway masks every inbound
    request, so a "raw" control arm sent through the gateway would arrive
    at the model already masked and the two arms would be identical — the
    control would measure nothing. Both arms therefore use the same direct
    provider call and differ ONLY by the text this harness masked itself.

    This raw path is eval-only, is not reachable from product code, and
    carries 100% synthetic corpus text (rule: no real customer data).

    The provider key is resolved through the gateway's own
    ``provider_key_service`` and is never logged, printed, or written to a
    results file.

    ``--via-gateway`` (env ``MASKEVAL_VIA_GATEWAY=1``) switches to the
    gateway endpoint for the scan-rounds stage, which needs the real
    streaming path.
    """

    PROVIDER_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        # Gateway coordinates (used by the scan-rounds stage only).
        self.base = (base_url or os.environ.get("GATEWAY_URL") or "http://localhost:8080").rstrip("/")
        self.key = api_key or os.environ.get("GATEWAY_API_KEY") or ""
        self.model = CRITERIA["axis_a"]["answer_model"]
        self._provider_key: str | None = None

    def warmup(self) -> None:
        """Resolve the provider key on the CALLING thread.

        ``resolve_provider_key_async`` runs a statement on the process-wide
        async engine, whose pooled connections bind to the event loop that
        first used them. Calling it lazily from several worker threads (each
        spinning its own ``asyncio.run`` loop) deadlocks on that pool — the
        first axis-A run hung there for 15 minutes with zero output. Callers
        MUST call this once before fanning out.
        """
        self._resolve_provider_key()

    def _resolve_provider_key(self) -> str:
        if self._provider_key:
            return self._provider_key
        import asyncio

        from app.services.provider_key_service import resolve_provider_key_async

        key = asyncio.run(resolve_provider_key_async("openrouter"))
        if not key:
            raise RuntimeError("no usable openrouter key is configured")
        self._provider_key = key
        return key

    def ask(self, system: str, user: str, *, max_tokens: int | None = None, retries: int = 3) -> str:
        key = self._resolve_provider_key()
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": CRITERIA["axis_a"]["temperature"],
            "max_tokens": max_tokens or CRITERIA["axis_a"]["max_tokens"],
        }).encode("utf-8")
        last = ""
        for attempt in range(retries):
            req = urllib.request.Request(
                self.PROVIDER_URL,
                data=body,
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return (payload["choices"][0]["message"].get("content") or "").strip()
            except Exception as exc:  # noqa: BLE001
                last = type(exc).__name__
                time.sleep(1.5 * (attempt + 1))
        return f"__ERROR__:{last}"


def percentiles(values: list[float], ps: list[int]) -> dict[str, float]:
    if not values:
        return {f"p{p}": 0.0 for p in ps}
    s = sorted(values)
    out = {}
    for p in ps:
        idx = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
        out[f"p{p}"] = round(s[idx], 3)
    return out
