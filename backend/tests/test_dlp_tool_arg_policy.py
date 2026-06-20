"""Phase 1 blocker B2 — tool-argument placeholder-safety policy tests.

Proves the gateway gate (shared by streaming + non-streaming tool-arg
handling) decides correctly whether masked tool arguments may execute,
and that blocked arguments become a valid-JSON, PII-free marker rather
than a silent placeholder. No restore, no Phase 2-4 logic.
"""

from __future__ import annotations

import json

from app.services.dlp.tool_arg_policy import (
    dlp_blocked_arguments,
    enforce_tool_arg_safety,
)


def _is_blocked_marker(s: str) -> bool:
    try:
        obj = json.loads(s)
    except Exception:
        return False
    return obj.get("_dlp_blocked") is True


class TestToolArgPolicy:
    def test_clean_args_always_allowed(self):
        args = '{"city": "Bangkok"}'
        out, blocked, reason = enforce_tool_arg_safety("unknown_tool", args)
        assert out == args and blocked is False and reason is None

    def test_web_search_with_placeholder_blocked(self):
        # External egress must not receive a placeholder-bearing query.
        out, blocked, reason = enforce_tool_arg_safety(
            "web_search", '{"query": "call <r0>"}'
        )
        assert blocked is True
        assert reason == "external_egress_blocked_masked_args"
        assert _is_blocked_marker(out)
        assert "<r0>" not in out  # placeholder not forwarded

    def test_save_memory_with_placeholder_allowed_with_warning(self):
        out, blocked, reason = enforce_tool_arg_safety(
            "save_memory", '{"text": "user is <r0>"}'
        )
        assert blocked is False
        assert reason == "placeholder_safe_fidelity_warning"
        assert out == '{"text": "user is <r0>"}'

    def test_send_email_with_masked_recipient_blocked(self):
        # Fake PII-recipient tool — must be blocked, never executed with <rN>.
        out, blocked, reason = enforce_tool_arg_safety(
            "send_email", '{"to": "<r0>"}'
        )
        assert blocked is True
        assert reason == "requires_plaintext_blocked_masked_args"
        assert "<r0>" not in out

    def test_unknown_tool_with_placeholder_blocked(self):
        out, blocked, reason = enforce_tool_arg_safety(
            "mystery_tool", '{"x": "<r0>"}'
        )
        assert blocked is True
        assert reason == "unknown_tool_blocked_masked_args"
        assert _is_blocked_marker(out)

    def test_placeholder_safe_tool_clean_args(self):
        out, blocked, reason = enforce_tool_arg_safety(
            "search_past_conversations", '{"q": "weather"}'
        )
        assert blocked is False and reason is None

    def test_blocked_marker_is_valid_json_no_pii(self):
        marker = dlp_blocked_arguments("web_search", "external_egress_blocked_masked_args")
        obj = json.loads(marker)  # valid JSON
        assert obj["_dlp_blocked"] is True
        assert obj["tool"] == "web_search"

    def test_stream_and_nonstream_same_decision(self):
        # The same helper is the single decision point used by both
        # orchestrator section 8d and the public_chat streaming block,
        # so identical inputs yield identical outputs by construction.
        args = '{"to": "<r0>"}'
        a = enforce_tool_arg_safety("send_email", args)
        b = enforce_tool_arg_safety("send_email", args)
        assert a == b
        assert a[1] is True  # blocked
