"""Tool-argument placeholder-safety policy (Phase 1 blocker B2).

Background
----------
The DLP pipeline masks PII found in a model-emitted tool call's
``function.arguments`` to neutral placeholders (``<rN>``). Those masked
arguments are then forwarded to the client / tool executor (chat-edge),
which executes the tool. There is NO token restore in the executor (by
design in Phase 1 — trusted-tool restore is a later phase), so a tool
that needs the real value would receive a placeholder.

This module is a small, conservative gate that decides — per tool —
whether masked arguments containing placeholders are safe to execute:

- ``placeholder_safe``   : the tool operates fine on masked content
                           (internal conversation tools). Execute, but
                           memory-class tools log a fidelity warning.
- ``local_execution``    : first-party client-local tool (SRAN Desktop
                           agent). Execute; call sites may first restore
                           user-restorable inbound tokens into the
                           arguments (client-bound, like restored text).
- ``external_egress``    : the tool sends arguments to an external
                           service; placeholders must NOT be shipped
                           (they would either leak the masking scheme or
                           run a meaningless query). Block.
- ``requires_plaintext`` : the tool's behavior depends on the real value
                           (e.g. send_email recipient). Executing with a
                           placeholder is wrong. Block.
- unknown tool           : treated as UNSAFE → block if placeholders are
                           present.

It does NOT restore values, change the placeholder format, touch
detectors, or add any Phase 2-4 logic. When the arguments contain no
placeholder, every tool is allowed (clean args are unaffected).

Enforcement is gateway-only: a blocked tool call has its
``function.arguments`` replaced with a valid-JSON, PII-free DLP marker
(:func:`dlp_blocked_arguments`) so the client sees an explicit blocked
signal instead of a silent placeholder, and no raw value or placeholder
reaches the executor.
"""

from __future__ import annotations

import json

from app.services.dlp.transformer import contains_mask_token

# Policy categories.
PLACEHOLDER_SAFE = "placeholder_safe"
REQUIRES_PLAINTEXT = "requires_plaintext"
EXTERNAL_EGRESS = "external_egress"
INTERNAL_ONLY = "internal_only"
# First-party tools that execute ON THE CLIENT'S OWN MACHINE (SRAN
# Desktop's agentic tool set). Their arguments are client-bound — the
# same destination that already receives the RESTORE-policy-filtered
# restored text — and never egress to a third-party provider, so a
# masked placeholder must not hard-block them. Call sites MAY restore
# user-restorable inbound tokens into these arguments (same allowlist
# the text restore uses: profile output action == RESTORE, and never
# credential/secret — see orchestrator._compute_restore_allowed_tokens);
# any token that survives that filter ships as-is, like
# ``placeholder_safe``.
LOCAL_EXECUTION = "local_execution"

# Per-tool policy. Conservative by default: anything NOT listed here is
# treated as unsafe (blocked when its masked arguments carry a
# placeholder). The names below match chat-edge's registered tools
# (services/chat-edge-api/internal/service/tools.go) and the SRAN
# Desktop registry (SranDesktop.Infrastructure/Mcp/Tools/*Tool.cs),
# plus a few known future PII-recipient tools classified ahead of time.
# tests/test_dlp_tool_policy_parity.py sweeps both registries and fails
# CI when a first-party tool is added without a classification here.
TOOL_ARG_POLICY: dict[str, str] = {
    # Internal conversation tools — operate on already-DLP-governed
    # content; masked values are acceptable.
    "save_memory": PLACEHOLDER_SAFE,
    "list_memories": PLACEHOLDER_SAFE,
    "search_past_conversations": PLACEHOLDER_SAFE,
    "update_conversation_state": PLACEHOLDER_SAFE,
    # Image generation already runs on a DLP-masked prompt elsewhere in
    # the gateway, so masked placeholders in the prompt are consistent.
    "generate_image": PLACEHOLDER_SAFE,
    # Video generation (chat-edge Sprint 2A) — same pipeline shape and
    # same DLP treatment as generate_image, so the same classification.
    "generate_video": PLACEHOLDER_SAFE,
    # External egress — never ship placeholders to a third-party service.
    "web_search": EXTERNAL_EGRESS,
    # Known future PII-recipient tools (not yet registered) — classified
    # conservatively so they are blocked until trusted restore exists.
    "send_email": REQUIRES_PLAINTEXT,
    "crm_lookup": REQUIRES_PLAINTEXT,
    "payment": REQUIRES_PLAINTEXT,
    # First-party SRAN Desktop tools (FIX 8). All of these execute
    # locally on the end user's machine behind the desktop's own
    # consent + PathGuard gating; their arguments never reach a
    # third-party service. Names match the desktop registry
    # (SranDesktop.Infrastructure/Mcp/Tools/*Tool.cs). NOT listed on
    # purpose: the desktop's ``web_search`` (external egress, above)
    # and remote MCP connector tools (dynamic names, they call
    # external servers — they stay under the unknown-tool block).
    "run_command": LOCAL_EXECUTION,
    "run_python": LOCAL_EXECUTION,
    "read_file": LOCAL_EXECUTION,
    "write_file": LOCAL_EXECUTION,
    "edit_file": LOCAL_EXECUTION,
    "create_directory": LOCAL_EXECUTION,
    "move_file": LOCAL_EXECUTION,
    "list_directory": LOCAL_EXECUTION,
    "search_files": LOCAL_EXECUTION,
    "computer_screenshot": LOCAL_EXECUTION,
    "computer_click": LOCAL_EXECUTION,
    "computer_type": LOCAL_EXECUTION,
    "computer_key": LOCAL_EXECUTION,
    "computer_scroll": LOCAL_EXECUTION,
    "computer_drag": LOCAL_EXECUTION,
    "open_application": LOCAL_EXECUTION,
    "open_url": LOCAL_EXECUTION,
    "read_clipboard": LOCAL_EXECUTION,
    "write_clipboard": LOCAL_EXECUTION,
    "update_plan": LOCAL_EXECUTION,
    "remember": LOCAL_EXECUTION,
    "use_skill": LOCAL_EXECUTION,
    "run_subagent": LOCAL_EXECUTION,
    # Desktop document reader — reads a local file through PathGuard +
    # SensitiveReadGuard and returns its text into the already-DLP-
    # governed conversation. Same class as read_file (its sibling that
    # was classified in FIX 8; this one shipped later and was missed,
    # which surfaced as unknown_tool_blocked_masked_args in production).
    "read_document": LOCAL_EXECUTION,
    # Desktop screen/UIA READ layer + pixel-region zoom — arguments are
    # coordinates, element ids from the last read_screen_elements
    # snapshot, or a window title matched against local windows; nothing
    # in the arguments leaves the machine.
    "computer_zoom": LOCAL_EXECUTION,
    "get_computer_state": LOCAL_EXECUTION,
    "read_screen_elements": LOCAL_EXECUTION,
    "uia_click": LOCAL_EXECUTION,
    # Browser-bridge tools whose arguments stay on the machine: integer
    # refs/tab ids and enum filters, or (browser_find) a query matched
    # against the ALREADY-LOADED DOM by the local content script — the
    # query never reaches the website.
    "browser_click": LOCAL_EXECUTION,
    "browser_find": LOCAL_EXECUTION,
    "browser_read_page": LOCAL_EXECUTION,
    "browser_screenshot": LOCAL_EXECUTION,
    "browser_list_tabs": LOCAL_EXECUTION,
    # External egress — these arguments LEAVE the machine, so a restored
    # PII value would egress raw: web_fetch, browser_navigate and
    # browser_tabs (action=new) ship a URL to the network; browser_type
    # and uia_set_value write free text into a live web page / focused
    # UI field (the same channel their own credential guards treat as
    # egress). Placeholders are blocked here, never restored.
    "web_fetch": EXTERNAL_EGRESS,
    "browser_navigate": EXTERNAL_EGRESS,
    "browser_tabs": EXTERNAL_EGRESS,
    "browser_type": EXTERNAL_EGRESS,
    "uia_set_value": EXTERNAL_EGRESS,
}

# Tools for which executing on masked content is allowed but reduces
# fidelity (worth a warning so operators can see degraded behavior).
_FIDELITY_WARNING_TOOLS = frozenset({"save_memory"})


def tool_arg_policy(tool_name: str | None) -> str | None:
    """Return the policy category for a tool, or None if unknown."""
    if not tool_name:
        return None
    return TOOL_ARG_POLICY.get(tool_name)


def allows_placeholder_args(tool_name: str | None) -> bool:
    """True only for tools explicitly classified ``placeholder_safe``.

    Unknown tools return False (conservative).
    """
    return tool_arg_policy(tool_name) == PLACEHOLDER_SAFE


def is_local_execution_tool(tool_name: str | None) -> bool:
    """True for first-party client-local tools (``local_execution``).

    Call sites use this to decide whether tool arguments may have
    user-restorable inbound tokens restored before emit — the arguments
    are client-bound, exactly like the restored response text.
    """
    return tool_arg_policy(tool_name) == LOCAL_EXECUTION


def dlp_blocked_arguments(tool_name: str | None, reason: str) -> str:
    """Build the valid-JSON, PII-free replacement for blocked arguments.

    Contains no raw value and no placeholder token — only a stable
    marker the client can detect. Always parseable JSON so downstream
    JSON handling does not break.
    """
    return json.dumps(
        {
            "_dlp_blocked": True,
            "reason": reason,
            "tool": tool_name or "",
        }
    )


def enforce_tool_arg_safety(
    tool_name: str | None, masked_args: str
) -> tuple[str, bool, str | None]:
    """Decide whether masked tool arguments may be executed.

    Parameters
    ----------
    tool_name : str | None
        The tool/function name.
    masked_args : str
        The arguments string AFTER DLP masking (may contain ``<rN>``).

    Returns
    -------
    (final_args, blocked, reason)
        - ``final_args``: the arguments to emit. Equal to ``masked_args``
          when allowed; the DLP-blocked marker JSON when blocked.
        - ``blocked``: True if the tool must NOT execute on these args.
        - ``reason``: machine-readable reason code (or None when the
          arguments are clean / no decision needed).

    Rules:
    - No placeholder in args → always allowed (clean args, reason None).
    - Placeholder + ``placeholder_safe`` tool → allowed; reason is
      ``"placeholder_safe"`` or ``"placeholder_safe_fidelity_warning"``.
    - Placeholder + ``local_execution`` tool → allowed; reason is
      ``"local_execution"``.
    - Placeholder + external/requires-plaintext/unknown tool → blocked.
    """
    if not contains_mask_token(masked_args):
        return masked_args, False, None

    policy = tool_arg_policy(tool_name)
    if policy == PLACEHOLDER_SAFE:
        if tool_name in _FIDELITY_WARNING_TOOLS:
            return masked_args, False, "placeholder_safe_fidelity_warning"
        return masked_args, False, "placeholder_safe"

    if policy == LOCAL_EXECUTION:
        # Client-local execution — a leftover placeholder (a token the
        # restore allowlist refused, e.g. a credential) degrades that
        # one call, it never leaks; blocking would break the desktop
        # agent's entire local tool loop.
        return masked_args, False, "local_execution"

    if policy == EXTERNAL_EGRESS:
        reason = "external_egress_blocked_masked_args"
    elif policy == REQUIRES_PLAINTEXT:
        reason = "requires_plaintext_blocked_masked_args"
    else:  # unknown tool — conservative block
        reason = "unknown_tool_blocked_masked_args"

    return dlp_blocked_arguments(tool_name, reason), True, reason
