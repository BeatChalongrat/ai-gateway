// E4 — the consent/permission port. Pins the danger classifier table (by
// name), the approval-mode matrix (ChatOrchestrator.ApplyApprovalMode), the
// PermissionStore ladder (persisted → session → default), scope resolution
// (granted root wins over the raw path; shell = cwd) and the sensitive-read
// guard (a read outside every granted folder asks; inside does not).
//
// LIMITS: pure functions and an on-disk store. The prompt itself — the
// bridge consent.request/consent.respond round trip and the page's card —
// is exercised live by `--probe-e4 consent`, not here.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PermissionStore, applyApprovalMode, classifyDanger, dlpBlocked, readRequiresConsent, resolveScope, toolMeta } from "../src/main/permissions";

const HOME = "C:\\Users\\tester";
const GRANTS = ["C:\\Users\\tester\\Desktop\\project", "D:\\data"];

describe("ToolDangerClassifier", () => {
  it("safe-lists reads, research and meta tools by name", () => {
    for (const n of ["read_file", "list_directory", "search_files", "read_clipboard", "web_search", "web_fetch", "run_subagent", "update_plan", "use_skill", "remember", "search_past_conversations", "computer_zoom", "browser_read_page", "browser_find", "browser_screenshot", "browser_list_tabs"]) {
      expect(classifyDanger(n, toolMeta(n).category).isDangerous, n).toBe(false);
    }
  });
  it("marks the acting tools dangerous with their reason", () => {
    expect(classifyDanger("run_command", "Shell")).toEqual({ isDangerous: true, reasonKey: "danger.shell" });
    expect(classifyDanger("run_python", "Network")).toEqual({ isDangerous: true, reasonKey: "danger.code" });
    expect(classifyDanger("write_file", "FileSystem")).toEqual({ isDangerous: true, reasonKey: "danger.fileWrite" });
    expect(classifyDanger("computer_click", "Screen")).toEqual({ isDangerous: true, reasonKey: "danger.computerUse" });
    expect(classifyDanger("browser_click", "Browser")).toEqual({ isDangerous: true, reasonKey: "danger.browser" });
    expect(classifyDanger("write_clipboard", "Clipboard")).toEqual({ isDangerous: true, reasonKey: "danger.clipboard" });
    expect(classifyDanger("some_remote_mcp_tool", "Network")).toEqual({ isDangerous: true, reasonKey: "danger.connector" });
  });
  it("unknown (remote MCP) tools are Network/Ask — fail-safe", () => {
    expect(toolMeta("filesystem_read_directory")).toEqual({ name: "filesystem_read_directory", category: "Network", defaultPermission: "Ask" });
  });
});

describe("ApprovalMode", () => {
  const shell = toolMeta("run_command");
  const read = toolMeta("read_file");
  it("auto: dangerous needs consent, safe-with-consent-default becomes auto", () => {
    expect(applyApprovalMode("NeedsConsent", shell, "auto")).toBe("NeedsConsent");
    expect(applyApprovalMode("NeedsConsent", read, "auto")).toBe("Auto");
    expect(applyApprovalMode("Auto", shell, "auto")).toBe("Auto");
  });
  it("manual: every dangerous tool asks, even when auto-approved", () => {
    expect(applyApprovalMode("Auto", shell, "manual")).toBe("NeedsConsent");
    expect(applyApprovalMode("Auto", read, "manual")).toBe("Auto");
  });
  it("skip: everything auto except a hard deny", () => {
    expect(applyApprovalMode("NeedsConsent", shell, "skip")).toBe("Auto");
    expect(applyApprovalMode("Denied", shell, "skip")).toBe("Denied");
  });
});

describe("PermissionStore", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "e4-perm-"));
  const file = path.join(dir, "permissions.json");
  it("default → session → persisted ladder", () => {
    const s = new PermissionStore(file);
    const shell = toolMeta("run_command");
    expect(s.check(shell, "C:\\x")).toBe("NeedsConsent");
    s.recordSession("run_command", "C:\\x", "AllowAlways");
    expect(s.check(shell, "C:\\x")).toBe("Auto");
    expect(s.check(shell, "C:\\y")).toBe("NeedsConsent"); // scope-keyed
    s.persist("run_command", "C:\\y", "deny_always");
    expect(s.check(shell, "C:\\y")).toBe("Denied");
    const reloaded = new PermissionStore(file);
    expect(reloaded.check(shell, "C:\\y")).toBe("Denied"); // persisted
    expect(reloaded.check(shell, "C:\\x")).toBe("NeedsConsent"); // session did not persist
  });
});

describe("scope + sensitive read", () => {
  it("filesystem scope is the granted root containing the path, else the path", () => {
    expect(resolveScope(toolMeta("write_file"), { path: "C:\\Users\\tester\\Desktop\\project\\a\\b.txt" }, GRANTS, HOME, null)).toBe("C:\\Users\\tester\\Desktop\\project");
    expect(resolveScope(toolMeta("write_file"), { path: "C:\\elsewhere\\x.txt" }, GRANTS, HOME, null)).toBe("C:\\elsewhere\\x.txt");
    expect(resolveScope(toolMeta("write_file"), { path: "notes.txt" }, GRANTS, HOME, null)).toBe("C:\\Users\\tester\\Desktop\\project"); // FIX26: relative -> INTO the first grant -> scoped to it
  });
  it("shell scope is the working directory (arg, remembered cwd, home)", () => {
    expect(resolveScope(toolMeta("run_command"), { command: "dir", working_directory: "D:\\data\\sub" }, GRANTS, HOME, null)).toBe("D:\\data");
    expect(resolveScope(toolMeta("run_command"), { command: "dir" }, GRANTS, HOME, "C:\\Users\\tester\\Desktop\\project")).toBe("C:\\Users\\tester\\Desktop\\project");
    expect(resolveScope(toolMeta("run_command"), { command: "dir" }, GRANTS, HOME, null)).toBe("C:\\Users\\tester\\Desktop\\project"); // FIX26: default cwd = first grant
    expect(resolveScope(toolMeta("web_fetch"), { url: "x" }, GRANTS, HOME, null)).toBe("*");
  });
  it("a silent read outside every granted folder asks; inside does not; writes are not its business", () => {
    expect(readRequiresConsent(toolMeta("read_file"), { path: "C:\\Users\\tester\\.ssh\\id_rsa" }, GRANTS, HOME)).toBe(true);
    expect(readRequiresConsent(toolMeta("read_file"), { path: "D:\\data\\report.txt" }, GRANTS, HOME)).toBe(false);
    expect(readRequiresConsent(toolMeta("search_files"), { directory: "C:\\Users\\tester" }, GRANTS, HOME)).toBe(true);
    expect(readRequiresConsent(toolMeta("write_file"), { path: "C:\\Users\\tester\\.ssh\\x" }, GRANTS, HOME)).toBe(false);
  });
  it("the gateway's DLP marker is honoured", () => {
    expect(dlpBlocked({ _dlp_blocked: true, reason: "credential" })).toBe("credential");
    expect(dlpBlocked({ path: "x" })).toBeNull();
  });
});
