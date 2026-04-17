"""OpenAI Moderation endpoint client.

Calls https://api.openai.com/v1/moderations to classify harmful content
(hate, harassment, self-harm, sexual, violence, etc.).

Design constraints:
- Fail-open: any error (timeout, auth failure, network issue, malformed
  response) returns flagged=False with an `error` field set. Moderation
  is a best-effort safety net; infrastructure flakiness must not block
  legitimate requests.
- Input is capped at 4000 characters before sending. OpenAI's endpoint
  accepts longer input but capping keeps latency and cost predictable.
- The OpenAI moderation endpoint is FREE (no token billing), but it
  still requires a valid API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


MODERATION_ENDPOINT = "https://api.openai.com/v1/moderations"
MAX_INPUT_CHARS = 4000
DEFAULT_TIMEOUT = 5.0
SCORE_THRESHOLD = 0.1


@dataclass(frozen=True)
class ModerationResult:
    flagged: bool
    categories: dict[str, bool] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    error: str | None = None


class ModerationService:
    """Thin wrapper around OpenAI's /v1/moderations endpoint."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def check(self, text: str, api_key: str) -> ModerationResult:
        if not api_key:
            return ModerationResult(flagged=False, error="no_api_key")
        if not text:
            return ModerationResult(flagged=False)

        capped = text[:MAX_INPUT_CHARS]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    MODERATION_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"input": capped},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            return ModerationResult(flagged=False, error=f"timeout: {exc}"[:200])
        except httpx.HTTPStatusError as exc:
            return ModerationResult(
                flagged=False,
                error=f"http_{exc.response.status_code}",
            )
        except Exception as exc:
            return ModerationResult(flagged=False, error=f"{type(exc).__name__}: {exc}"[:200])

        try:
            result = payload["results"][0]
            flagged = bool(result.get("flagged", False))
            categories = {
                k: bool(v) for k, v in result.get("categories", {}).items() if v
            }
            scores = {
                k: round(float(v), 3)
                for k, v in result.get("category_scores", {}).items()
                if float(v) > SCORE_THRESHOLD
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return ModerationResult(
                flagged=False,
                error=f"parse_error: {type(exc).__name__}",
            )

        return ModerationResult(
            flagged=flagged,
            categories=categories,
            scores=scores,
        )
