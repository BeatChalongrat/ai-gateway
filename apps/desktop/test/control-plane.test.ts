import { describe, expect, it } from "vitest";

import { HOST_TOOL_DEFINITIONS } from "../src/main/host-tools";
import { BROWSER_BRIDGE_TOOLS, HOST_QUERY_OPS } from "../src/shared/bridge-protocol";

// FIX2 §3.6 (host half): the control plane (host.query ops) and the model's tool
// vocabulary are DISJOINT namespaces, and the closed page op-set is PINNED — a
// new op cannot be added silently. The sidecar half (tools/list never contains a
// control-plane op) is asserted in C# (Fix1BrowserSeparationTests): the sidecar
// registers tools only via McpToolRegistry.Register, and host/query is a separate
// JSON-RPC method that ListTools() never enumerates, so the namespaces cannot cross.
describe("FIX2 §3.6 — the control plane is never a tool, and the op-set is pinned", () => {
  // Every op the PAGE may send, enumerated explicitly. The next person to add an
  // op to HOST_QUERY_OPS must add it here too — which is the moment to decide it
  // is page-only control-plane and NEVER a tool. Adding to HOST_QUERY_OPS without
  // adding here fails `the page op-set is exactly this pinned list`.
  const PAGE_ONLY_OPS = [
    "session.logout",
    "diag.reportRendererError",
    "skills.list", "skills.setEnabled", "skills.installPicker", "skills.delete",
    "tasks.list", "tasks.create", "tasks.update", "tasks.setEnabled", "tasks.delete", "tasks.runNow", "tasks.runs",
    "connectors.listCatalog", "connectors.listInstalled", "connectors.install", "connectors.uninstall",
    "connectors.oauthConnect", "connectors.oauthDisconnect", "connectors.addCustomDialog",
    "settings.get", "settings.set",
    "folders.list", "folders.pickAndGrant", "folders.revoke",
    "globalNotes.list", "globalNotes.delete",
    "browser.pickExtensionFolder",
    // FIX6: the approved-site list is control-plane. The page may list and revoke;
    // there is NO sites.add — a site is approved only through the consent prompt.
    "sites.list", "sites.revoke",
  ];

  // Host↔sidecar INTERNAL ops (a leading _): never in the page's vocabulary, never tools.
  const INTERNAL_OPS = [
    "skills._install", "tasks._due", "tasks._recordRun",
    "connectors._installApiKey", "connectors._testCustom", "connectors._installCustom",
    "browser.applyExtensionFolder",
    "notes._forPrompt", // FIX5 §1 — host reads the sidecar's local notes for the system prompt
    "browser._siteForAction", // FIX6 — host resolves a browser action's target origin from the live tab
    "tasks._catchUp", "tasks._recordSkip", "tasks._reschedule", // FIX8 A1 — missed-run catch-up, skip recording, self-reschedule
  ];

  const controlPlane = new Set<string>([...PAGE_ONLY_OPS, ...INTERNAL_OPS]);
  const hostToolNames = Object.values(HOST_TOOL_DEFINITIONS).map((d) => d.name);

  it("the page op-set is exactly this pinned list (a new op forces a decision)", () => {
    expect([...HOST_QUERY_OPS].sort()).toEqual([...PAGE_ONLY_OPS].sort());
  });

  it("no host-side tool shares a name with a control-plane op", () => {
    expect(hostToolNames.filter((n) => controlPlane.has(n))).toEqual([]);
  });

  it("no gated browser tool shares a name with a control-plane op", () => {
    expect([...BROWSER_BRIDGE_TOOLS].filter((n) => controlPlane.has(n))).toEqual([]);
  });

  it("control-plane ops are dotted; tool names are snake_case (no accidental overlap)", () => {
    for (const op of controlPlane) expect(op).toContain(".");
    for (const t of [...hostToolNames, ...BROWSER_BRIDGE_TOOLS]) expect(t).not.toContain(".");
  });

  it("internal (_) ops are host↔sidecar only — never in the page's vocabulary", () => {
    for (const op of INTERNAL_OPS) expect(HOST_QUERY_OPS).not.toContain(op);
  });

  it("the browser-bridge gate list is exactly the 8 pipe tools (open_url excluded)", () => {
    expect([...BROWSER_BRIDGE_TOOLS].sort()).toEqual(
      [
        "browser_click", "browser_find", "browser_list_tabs", "browser_navigate",
        "browser_read_page", "browser_screenshot", "browser_tabs", "browser_type",
      ].sort(),
    );
    expect(BROWSER_BRIDGE_TOOLS.has("open_url")).toBe(false);
  });
});
