"""FIX35 Part C step 1 — task classification from ONE asset.

``task_taxonomy.json`` (next to this module) is the single source of the
~30 task types, their signals, capability needs, preferred band and the
cold-start default ranking. Nothing else in any service carries a copy;
``tests/test_auto_router.py`` pins that.

The classifier is a deliberately light heuristic (substring hits with per-
task weights, Thai-aware: Thai has no word boundaries so substrings ARE the
unit), not an LLM call — routing must cost nothing and an operator must be
able to reconstruct why a turn was classed the way it was from the log line
(``auto_route`` carries ``task``, ``confidence``, ``signals``).

It never raises: any failure → ``general_chat`` with confidence 0, and the
router treats that as "use the default set".
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_ASSET = Path(__file__).with_name("task_taxonomy.json")
_lock = threading.Lock()
_cache: dict[str, Any] | None = None

GENERAL = "general_chat"


def load_taxonomy() -> dict[str, Any]:
    """Parsed asset, cached for the process. Raises only on a broken asset
    (caught by the router, which then uses its hardcoded default set)."""
    global _cache
    with _lock:
        if _cache is None:
            with _ASSET.open(encoding="utf-8") as fh:
                _cache = json.load(fh)
        return _cache


def task_ids() -> list[str]:
    return list(load_taxonomy()["tasks"].keys())


def task_spec(task: str) -> dict[str, Any]:
    tasks = load_taxonomy()["tasks"]
    return tasks.get(task) or tasks[GENERAL]


DEFAULT_GROUP = "chat_light"


def task_group(task: str) -> str:
    """FIX36 A1 - the rank group a task belongs to (asset ``tasks.*.group``).
    Unknown task or missing mapping -> the general-chat group, so ranking and
    priors always have a bucket to land in."""
    return str(task_spec(task).get("group") or DEFAULT_GROUP)


def group_prior(group: str) -> list[str]:
    """Ordered configurable prior for a rank group (asset ``rank_groups``).
    Empty when the asset has no such group - the router then falls back to
    admin preference / cost, never raises."""
    groups = load_taxonomy().get("rank_groups") or {}
    spec = groups.get(group) or {}
    prior = spec.get("prior") or []
    return [str(m) for m in prior]


def exploration_rate() -> float:
    """FIX36 A3 knob (asset ``exploration_rate``), clamped to [0, 0.5]."""
    try:
        raw = float(load_taxonomy().get("exploration_rate", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(max(raw, 0.0), 0.5)


def prior_weight_samples() -> int:
    """FIX36 A2 knob K in w = n/(n+K): how many live samples it takes for
    tenant data to outweigh the configured prior. Asset
    ``prior_weight_samples``; >=1."""
    try:
        raw = int(load_taxonomy().get("prior_weight_samples", 20))
    except (TypeError, ValueError):
        return 20
    return max(raw, 1)


@dataclass
class TaskClass:
    task: str
    confidence: float          # 0..1 — share of the winning score
    score: float
    signals: list[str] = field(default_factory=list)
    language: str = "en"       # "th" when the text is Thai-dominant


_CODE_FENCE = re.compile(r"```|\bdef \w+\(|\bfunction\s+\w+\(|\bimport \w+|\bSELECT\b.+\bFROM\b", re.I | re.S)


def _last_user_text(messages: Iterable[dict]) -> str:
    for m in reversed(list(messages)):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        return ""
    return ""


def _is_thai(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    thai = sum(1 for ch in stripped if "฀" <= ch <= "๿")
    return thai >= 0.3 * len(stripped)


def classify_task(messages: Iterable[dict], *, has_images: bool = False, has_tools: bool = False) -> TaskClass:
    """Class the LAST user message. Never raises."""
    try:
        text = _last_user_text(messages)
        lowered = text.lower()
        language = "th" if _is_thai(text) else "en"
        tasks = load_taxonomy()["tasks"]
        scores: dict[str, float] = {}
        hits: dict[str, list[str]] = {}
        for task, spec in tasks.items():
            weight = float(spec.get("weight", 1) or 1)
            for sig in spec.get("signals", []):
                if sig and sig.lower() in lowered:
                    scores[task] = scores.get(task, 0.0) + weight
                    hits.setdefault(task, []).append(sig)
        # Structural signals the text alone can't show.
        if has_images:
            scores["vision_image"] = scores.get("vision_image", 0.0) + 3.0
            hits.setdefault("vision_image", []).append("<image attached>")
        if _CODE_FENCE.search(text):
            for t in ("code_debug", "code_review", "code_explain", "code_generate", "sql_query"):
                if t in scores:
                    scores[t] += 2.0
            if not any(t in scores for t in ("code_debug", "code_review", "code_explain", "code_generate", "sql_query")):
                scores["code_generate"] = scores.get("code_generate", 0.0) + 2.0
                hits.setdefault("code_generate", []).append("<code block>")
        if has_tools and "agent_multistep" in scores:
            scores["agent_multistep"] += 2.0
        if not scores:
            return TaskClass(task=GENERAL, confidence=0.0, score=0.0, language=language)
        task, best = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
        total = sum(scores.values()) or 1.0
        return TaskClass(
            task=task,
            confidence=round(best / total, 3),
            score=best,
            signals=hits.get(task, [])[:6],
            language=language,
        )
    except Exception:  # noqa: BLE001 — classification must never break routing
        return TaskClass(task=GENERAL, confidence=0.0, score=0.0)
