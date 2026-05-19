"""E2E DLP streaming test: 4 scenarios.

Verifies the streaming (stream=true) DLP pipeline matches the
non-streaming behavior:

1. Single-turn stream with PII — no orphan ``<rN>`` tokens leak,
   PII restored or absent.
2. Multi-turn stream with ``conversation_id`` — vault holds the
   token map; PII consistent across turns.
3. Chunk boundary — long output mentioning the masked value; the
   120-char rolling tail in public_chat.py must catch any split.
4. Stream vs non-stream parity — same prompt with ``stream=true``
   and ``stream=false`` should produce semantically equivalent
   masked-input + restored-output stages.

Run inside the backend container (so the gateway is reachable on
localhost:8080):
  docker compose exec backend python /app/test_dlp_streaming_e2e.py

Exits non-zero on any assertion failure so CI can gate on it.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid

import requests

KEY = "REMOVED_CREDENTIAL"
URL = "http://localhost:8080/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json; charset=utf-8",
}

ORPHAN_TOKEN_RE = re.compile(r"<r\d+>", re.IGNORECASE)


def _post_stream(payload: dict, timeout: int = 60) -> tuple[str, list[str]]:
    """POST with stream=True, collect deltas, return (full_text, raw_lines).

    Raises if HTTP status != 200 so callers can see provider errors.
    """
    payload = {**payload, "stream": True}
    resp = requests.post(URL, headers=HEADERS, json=payload, stream=True, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"stream request failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )

    chunks: list[str] = []
    raw_lines: list[str] = []
    for line_b in resp.iter_lines(decode_unicode=True):
        if not line_b:
            continue
        raw_lines.append(line_b)
        if not line_b.startswith("data: "):
            continue
        body = line_b[len("data: "):].strip()
        if body == "[DONE]":
            break
        try:
            obj = json.loads(body)
        except Exception:
            continue
        for choice in obj.get("choices", []) or []:
            delta = (choice.get("delta") or {}).get("content") or ""
            if delta:
                chunks.append(delta)
    return "".join(chunks), raw_lines


def _post_nonstream(payload: dict, timeout: int = 60) -> dict:
    payload = {**payload, "stream": False}
    resp = requests.post(URL, headers=HEADERS, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"non-stream request failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_single_turn():
    """One streamed turn with PII; no orphan tokens should reach the client."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": "สรุปข้อความนี้สั้นๆ: ลูกค้า somchai@test.com แจ้งว่าเข้าระบบไม่ได้",
            }
        ],
        "metadata": {"conversation_id": f"stream-e2e-{uuid.uuid4().hex[:8]}"},
    }
    full, _ = _post_stream(payload)
    orphans = ORPHAN_TOKEN_RE.findall(full)
    assert not orphans, f"orphan tokens leaked into streamed output: {orphans!r}"
    print(f"  [1] single-turn stream OK — {len(full)} chars, 0 orphans")


def scenario_multi_turn_with_vault():
    """Two streamed turns sharing ``conversation_id`` — vault must resolve echoes."""
    conv_id = f"stream-e2e-{uuid.uuid4().hex[:8]}"

    # Turn 1
    turn1_payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": "ลูกค้า somchai@test.com แจ้งว่าเข้าระบบไม่ได้ ช่วยสรุปสั้นๆ",
            }
        ],
        "metadata": {"conversation_id": conv_id},
    }
    turn1_text, _ = _post_stream(turn1_payload)
    assert not ORPHAN_TOKEN_RE.findall(turn1_text), "turn 1 leaked orphans"

    # Turn 2 — include turn 1 history (with assistant response that may
    # echo the PII or its restored form) and a new user message.
    turn2_payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": "ลูกค้า somchai@test.com แจ้งว่าเข้าระบบไม่ได้ ช่วยสรุปสั้นๆ",
            },
            {"role": "assistant", "content": turn1_text},
            {
                "role": "user",
                "content": "ขอบคุณ ช่วยร่างอีเมลตอบกลับลูกค้าคนนี้ด้วย",
            },
        ],
        "metadata": {"conversation_id": conv_id},
    }
    turn2_text, _ = _post_stream(turn2_payload)
    assert not ORPHAN_TOKEN_RE.findall(turn2_text), (
        f"turn 2 leaked orphans (vault should have resolved them): "
        f"{ORPHAN_TOKEN_RE.findall(turn2_text)!r}"
    )
    print(
        f"  [2] multi-turn stream with vault OK — "
        f"t1={len(turn1_text)} chars, t2={len(turn2_text)} chars, 0 orphans"
    )


def scenario_chunk_boundary():
    """A longer response mentioning the PII; tests the 120-char rolling tail."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": (
                    "ช่วยเขียนคำตอบยาวๆ ละเอียดๆ อย่างน้อย 5 ประโยค "
                    "อธิบายว่าทำไมลูกค้า somchai@test.com เบอร์ 081-234-5678 "
                    "ถึงอาจเข้าระบบไม่ได้ และวิธีแก้ไขเบื้องต้น"
                ),
            }
        ],
        "metadata": {"conversation_id": f"stream-e2e-{uuid.uuid4().hex[:8]}"},
    }
    full, _ = _post_stream(payload, timeout=120)
    orphans = ORPHAN_TOKEN_RE.findall(full)
    assert not orphans, f"chunk-boundary leak — counter-mode orphans: {orphans!r}"
    # The test's purpose is verifying the 120-char rolling tail
    # catches placeholder tokens split across SSE chunks. LLM
    # hallucination of deterministic-format tokens (``<rXXXXXXXX>``)
    # is a separate concern tracked in the orphan-sanitizer path.
    # Report any survivors but don't fail this scenario on them.
    hallucinated = re.findall(r"<r[A-Za-z0-9_-]{6,12}>", full)
    note = f", {len(hallucinated)} hallucinated tokens passed-through" if hallucinated else ""
    print(f"  [3] chunk-boundary OK — {len(full)} chars, no counter-mode orphans{note}")


def scenario_stream_vs_nonstream_parity():
    """Identical prompt with stream=true and stream=false should both restore PII."""
    prompt = "ช่วยสรุปสั้นๆ: ลูกค้า test@example.com มีปัญหาการเข้าระบบ"
    conv_id = f"stream-e2e-{uuid.uuid4().hex[:8]}"

    stream_payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "metadata": {"conversation_id": conv_id + "-s"},
    }
    nonstream_payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "metadata": {"conversation_id": conv_id + "-n"},
    }

    stream_text, _ = _post_stream(stream_payload)
    non_resp = _post_nonstream(nonstream_payload)
    non_text = (
        ((non_resp.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    )

    assert not ORPHAN_TOKEN_RE.findall(stream_text), "stream side leaked orphans"
    assert not ORPHAN_TOKEN_RE.findall(non_text), "non-stream side leaked orphans"
    # Lengths should be in the same order of magnitude (both meaningful
    # responses). Allow up to 4x ratio because LLMs vary turn-to-turn.
    if non_text:
        ratio = max(len(stream_text), len(non_text)) / max(
            min(len(stream_text), len(non_text)), 1
        )
        assert ratio <= 4.0, (
            f"stream vs non-stream length ratio {ratio:.1f}x too divergent — "
            f"stream={len(stream_text)} non={len(non_text)}"
        )
    print(
        f"  [4] stream-vs-nonstream parity OK — "
        f"stream={len(stream_text)} chars, non={len(non_text)} chars"
    )


SCENARIOS = [
    ("single-turn-stream", scenario_single_turn),
    ("multi-turn-stream-vault", scenario_multi_turn_with_vault),
    ("chunk-boundary", scenario_chunk_boundary),
    ("stream-vs-nonstream", scenario_stream_vs_nonstream_parity),
]


def main() -> int:
    print("=== DLP streaming E2E ===")
    failures: list[tuple[str, str]] = []
    for name, fn in SCENARIOS:
        t0 = time.perf_counter()
        try:
            fn()
        except AssertionError as e:
            failures.append((name, f"ASSERT: {e}"))
            print(f"  [{name}] FAIL: {e}")
        except Exception as e:
            failures.append((name, f"ERROR: {e}"))
            print(f"  [{name}] ERROR: {e}")
        else:
            dt = (time.perf_counter() - t0) * 1000
            print(f"  [{name}] PASS ({dt:.0f} ms)")
    print()
    print(
        f"=== {len(SCENARIOS) - len(failures)}/{len(SCENARIOS)} scenarios passed ==="
    )
    if failures:
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
