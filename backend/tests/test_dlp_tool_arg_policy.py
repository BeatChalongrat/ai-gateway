"""Phase 1 blocker B2 — tool-argument placeholder-safety policy tests.

Proves the gateway gate (shared by streaming + non-streaming tool-arg
handling) decides correctly whether masked tool arguments may execute,
and that blocked arguments become a valid-JSON, PII-free marker rather
than a silent placeholder. No restore, no Phase 2-4 logic.
"""

from __future__ import annotations

import json

import pytest

from app.services.dlp.tool_arg_policy import (
    dlp_blocked_arguments,
    enforce_tool_arg_safety,
    is_local_execution_tool,
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

    def test_connector_tool_with_detected_content_blocked(self):
        # FIX12 §2.3 — a remote MCP connector tool (mcp__ namespace) that the
        # scanner DETECTED PII in (so its arg carries a mask token) must be
        # blocked as external egress, EXPLICITLY (not via the unknown default).
        out, blocked, reason = enforce_tool_arg_safety(
            "mcp__slack__post_message", '{"text": "ping <r0> about the invoice"}'
        )
        assert blocked is True
        assert reason == "external_egress_blocked_masked_args"
        assert _is_blocked_marker(out)
        assert "<r0>" not in out  # nothing masked reaches the third party

    def test_connector_tool_with_clean_content_allowed(self):
        # The comparison that makes the above mean something: the SAME connector
        # tool with content the scanner did NOT detect (no mask token) passes
        # through unchanged — the connector works for clean content.
        args = '{"text": "ping the team about lunch"}'
        out, blocked, reason = enforce_tool_arg_safety(
            "mcp__slack__post_message", args
        )
        assert blocked is False and reason is None
        assert out == args
        # And it is never treated as client-local execution.
        assert is_local_execution_tool("mcp__slack__post_message") is False

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

    # ------------------------------------------------------------------
    # FIX 8 — first-party SRAN Desktop local tools must never be
    # hard-blocked as "unknown" (they were: run_command with a masked
    # 127.0.0.1 came back ``unknown_tool_blocked_masked_args`` and the
    # desktop agent could not run anything).
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "tool",
        [
            "run_command",
            "run_python",
            "read_file",
            "write_file",
            "edit_file",
            "create_directory",
            "move_file",
            "list_directory",
            "search_files",
            "computer_screenshot",
            "computer_click",
            "computer_type",
            "computer_key",
            "computer_scroll",
            "computer_drag",
            "open_application",
            "open_url",
            "read_clipboard",
            "write_clipboard",
            "update_plan",
            "remember",
            "use_skill",
            "run_subagent",
            # Tool-policy gap fix (after 831efd3): desktop tools that
            # shipped after FIX 8 and were falling into the unknown-tool
            # block. All argument-local: file path via PathGuard
            # (read_document), coordinates / element ids / window titles
            # (screen + UIA read layer), integer refs / DOM-local query
            # (browser read layer).
            "read_document",
            # FIX73: the question card's arguments are shown to the user on
            # their own machine; a masked place/name/date in a question must
            # not block the card (it did: unknown_tool_blocked_masked_args).
            "ask_user_question",
            "computer_zoom",
            "get_computer_state",
            "read_screen_elements",
            "uia_click",
            "browser_click",
            "browser_find",
            "browser_read_page",
            "browser_screenshot",
            "browser_list_tabs",
        ],
    )
    def test_desktop_local_tool_with_placeholder_allowed(self, tool):
        args = '{"command": "ping -n 4 <r0>"}'
        out, blocked, reason = enforce_tool_arg_safety(tool, args)
        assert blocked is False
        assert reason == "local_execution"
        assert out == args  # args pass through unchanged (restore is the
        # call sites' job — this gate only decides execute/block)
        assert is_local_execution_tool(tool) is True

    def test_local_execution_does_not_leak_to_other_tools(self):
        # web_search is the desktop's external-egress tool — a masked
        # query must STILL be blocked, and genuinely unknown tools
        # (e.g. remote MCP connector tools) stay under the conservative
        # unknown-tool block.
        assert is_local_execution_tool("web_search") is False
        assert is_local_execution_tool("some_remote_mcp_tool") is False
        assert is_local_execution_tool(None) is False

        _, blocked, reason = enforce_tool_arg_safety(
            "web_search", '{"query": "<r0>"}'
        )
        assert blocked is True and reason == "external_egress_blocked_masked_args"

        out, blocked, reason = enforce_tool_arg_safety(
            "some_remote_mcp_tool", '{"x": "<r0>"}'
        )
        assert blocked is True and reason == "unknown_tool_blocked_masked_args"
        assert _is_blocked_marker(out)

    # ------------------------------------------------------------------
    # Tool-policy gap fix — regression for the live incident
    # (read_document blocked as unknown) and the egress side of the new
    # desktop classifications.
    # ------------------------------------------------------------------

    def test_read_document_with_echoed_inbound_token_allowed(self):
        # The live incident: the model echoed an inbound mask token
        # (<<CATEGORY_N>> shape, e.g. a masked person name inside the
        # filename) into the path argument. read_document must execute
        # (restore is the call sites' job), not come back as a blocked
        # marker.
        args = '{"path": "C:\\\\Users\\\\me\\\\IDP_<<PERSON_1>>.xlsx"}'
        out, blocked, reason = enforce_tool_arg_safety("read_document", args)
        assert blocked is False
        assert reason == "local_execution"
        assert out == args
        assert is_local_execution_tool("read_document") is True

    def test_ask_user_question_with_masked_entity_still_reaches_the_card(self):
        # FIX73: a question whose option names a masked entity is client-bound
        # (rendered by the desktop for the user) — allowed, like read_document.
        args = '{"questions": [{"question": "Which trip?", "header": "Trip", "options": [{"label": "<r0> in spring"}, {"label": "<r1> later"}]}]}'
        out, blocked, reason = enforce_tool_arg_safety("ask_user_question", args)
        assert blocked is False
        assert reason == "local_execution"
        assert out == args
        assert is_local_execution_tool("ask_user_question") is True

    @pytest.mark.parametrize(
        "tool, args",
        [
            ("web_fetch", '{"url": "https://x.test/?q=<r0>"}'),
            ("browser_navigate", '{"url": "https://x.test/<r0>"}'),
            ("browser_tabs", '{"action": "new", "url": "https://x.test/<r0>"}'),
            ("browser_type", '{"ref": 3, "text": "<r0>"}'),
            ("uia_set_value", '{"element_id": 7, "text": "<r0>"}'),
        ],
    )
    def test_desktop_egress_tool_with_placeholder_blocked(self, tool, args):
        # Arguments that leave the machine (URL to the network, free text
        # into a live page / UI field) must never carry a placeholder —
        # and must never be candidates for restore.
        out, blocked, reason = enforce_tool_arg_safety(tool, args)
        assert blocked is True
        assert reason == "external_egress_blocked_masked_args"
        assert _is_blocked_marker(out)
        assert "<r0>" not in out
        assert is_local_execution_tool(tool) is False

    def test_generate_video_placeholder_safe_like_generate_image(self):
        args = '{"prompt": "a person named <r0> waves"}'
        out, blocked, reason = enforce_tool_arg_safety("generate_video", args)
        assert blocked is False
        assert reason == "placeholder_safe"
        assert out == args

    def test_local_tool_clean_args_untouched(self):
        args = '{"command": "ping -n 4 localhost"}'
        out, blocked, reason = enforce_tool_arg_safety("run_command", args)
        assert out == args and blocked is False and reason is None

    def test_stream_and_nonstream_same_decision(self):
        # The same helper is the single decision point used by both
        # orchestrator section 8d and the public_chat streaming block,
        # so identical inputs yield identical outputs by construction.
        args = '{"to": "<r0>"}'
        a = enforce_tool_arg_safety("send_email", args)
        b = enforce_tool_arg_safety("send_email", args)
        assert a == b
        assert a[1] is True  # blocked
