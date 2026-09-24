"""Tool-policy parity CI gate.

Machine-checks that every tool a first-party client can advertise to the
model is consciously classified in ``TOOL_ARG_POLICY``
(``app/services/dlp/tool_arg_policy.py``). An unclassified tool falls
into the conservative unknown-tool block, which is correct for remote
MCP connector tools (dynamic names, external servers) but a production
outage for our own tools: ``read_document`` shipped in the desktop
registry without a policy entry and every call whose masked arguments
carried a placeholder came back ``unknown_tool_blocked_masked_args``.

This test moves that failure from runtime (a user's blocked tool call)
to CI: add a tool to either registry below and the sweep stays red until
a human classifies it — local_execution / external_egress /
placeholder_safe / requires_plaintext is a security decision that must
be made consciously (see the classification criteria in
tool_arg_policy.py).

Registries swept:
- SRAN Desktop: ``SranDesktop/src/SranDesktop.Infrastructure/Mcp/Tools/*.cs``
  (every IMcpTool lives in that directory; names are declared either as
  ``public string Name => "..."`` or via ``public const string ToolName``).
- chat-edge (web chat): ``services/chat-edge-api/internal/service/tools.go``
  (all static GatewayTool defs used by ToolExecutor.ToolDefs).

Deliberately NOT swept: remote MCP connector tools (dynamic names — the
unknown-tool block is their intended treatment).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.services.dlp.tool_arg_policy import TOOL_ARG_POLICY

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DESKTOP_TOOLS_DIR = (
    REPO_ROOT / "SranDesktop" / "src" / "SranDesktop.Infrastructure" / "Mcp" / "Tools"
)
# FIX128 — the Electron desktop's tool list is the .NET tools sidecar (the
# directory above, still what the sidecar serves) PLUS the host-side tools the
# Electron main process defines itself. Until FIX128 only the sidecar was
# scanned, against a >= 30 floor written for the retired WPF shell (39 tools):
# the sidecar alone has 25, so a full checkout failed the liveness check.
_DESKTOP_MAIN = REPO_ROOT / "apps" / "desktop" / "src" / "main"
# Files that declare host tools: definitions (``name: "…"``) in the first two,
# the question tool's constant in the third.
DESKTOP_HOST_TOOL_FILES = [_DESKTOP_MAIN / "host-tools.ts", _DESKTOP_MAIN / "turn-coordinator.ts"]
DESKTOP_ASK_USER_TS = _DESKTOP_MAIN / "ask-user.ts"
CHAT_EDGE_TOOLS_GO = (
    REPO_ROOT / "services" / "chat-edge-api" / "internal" / "service" / "tools.go"
)

# Name declarations in the desktop tool classes. Both shapes are real:
# most tools inline the literal; SpawnAgentTool routes through a const
# (``public const string ToolName = "run_subagent"``).
_CS_NAME_RE = re.compile(r'public\s+string\s+Name\s*=>\s*"([a-z0-9_]+)"\s*;')
_CS_TOOLNAME_CONST_RE = re.compile(
    r'public\s+const\s+string\s+ToolName\s*=\s*"([a-z0-9_]+)"\s*;'
)
# GatewayFunction defs in chat-edge tools.go. The [a-z0-9_] class keeps
# this scoped to tool names (Go field names / camelCase values no-match).
_GO_NAME_RE = re.compile(r'Name:\s*"([a-z0-9_]+)"')
# Tool definitions in the Electron host: ``name: "read_clipboard",``.
_TS_NAME_RE = re.compile(r'(?<![A-Za-z_.])name:\s*"([a-z0-9_]+)"')
# The question tool is declared by constant in the turn coordinator.
_TS_ASK_RE = re.compile(r'ASK_USER_TOOL\s*=\s*"([a-z0-9_]+)"')


def _desktop_tool_names() -> set[str]:
    assert DESKTOP_TOOLS_DIR.is_dir(), (
        f"Desktop tool registry not found at {DESKTOP_TOOLS_DIR} — if the "
        "directory moved, update this test so the parity gate keeps running."
    )
    names: set[str] = set()
    for cs in DESKTOP_TOOLS_DIR.glob("*.cs"):
        text = cs.read_text(encoding="utf-8-sig", errors="replace")
        names.update(_CS_NAME_RE.findall(text))
        names.update(_CS_TOOLNAME_CONST_RE.findall(text))
    for ts in DESKTOP_HOST_TOOL_FILES:
        if ts.is_file():
            names.update(_TS_NAME_RE.findall(ts.read_text(encoding="utf-8", errors="replace")))
    if DESKTOP_ASK_USER_TS.is_file():
        names.update(_TS_ASK_RE.findall(DESKTOP_ASK_USER_TS.read_text(encoding="utf-8", errors="replace")))
    return names


def _chat_edge_tool_names() -> set[str]:
    assert CHAT_EDGE_TOOLS_GO.is_file(), (
        f"chat-edge tool registry not found at {CHAT_EDGE_TOOLS_GO} — if the "
        "file moved, update this test so the parity gate keeps running."
    )
    text = CHAT_EDGE_TOOLS_GO.read_text(encoding="utf-8", errors="replace")
    return set(_GO_NAME_RE.findall(text))


class TestToolPolicyParity:
    def test_desktop_registry_scan_is_alive(self):
        # Guard against regex/layout rot silently emptying the sweep —
        # an empty registry set would make the parity test pass while
        # checking nothing. The desktop registry has ~39 tools today.
        names = _desktop_tool_names()
        assert len(names) >= 30, (
            f"Desktop tool scan found only {sorted(names)} — the Name "
            "declaration pattern or directory layout likely changed; fix the "
            "scan, do not delete this assertion."
        )
        # Spot-check names we know exist on both classification sides.
        assert "read_file" in names
        assert "web_fetch" in names

    def test_chat_edge_registry_scan_is_alive(self):
        names = _chat_edge_tool_names()
        assert len(names) >= 5, (
            f"chat-edge tool scan found only {sorted(names)} — the Name "
            "field pattern in tools.go likely changed; fix the scan, do not "
            "delete this assertion."
        )
        assert "web_search" in names

    def test_every_desktop_tool_is_classified(self):
        unclassified = _desktop_tool_names() - set(TOOL_ARG_POLICY)
        assert not unclassified, (
            f"Desktop tools missing from TOOL_ARG_POLICY: {sorted(unclassified)}. "
            "Unclassified first-party tools are hard-blocked at runtime the "
            "moment their masked arguments carry a placeholder "
            "(unknown_tool_blocked_masked_args). Classify each one in "
            "app/services/dlp/tool_arg_policy.py — local_execution ONLY if no "
            "argument can leave the machine; anything that ships a URL or "
            "free text off the machine is external_egress."
        )

    def test_every_chat_edge_tool_is_classified(self):
        unclassified = _chat_edge_tool_names() - set(TOOL_ARG_POLICY)
        assert not unclassified, (
            f"chat-edge tools missing from TOOL_ARG_POLICY: {sorted(unclassified)}. "
            "Classify each one in app/services/dlp/tool_arg_policy.py before "
            "shipping — see that module's docstring for the categories."
        )
