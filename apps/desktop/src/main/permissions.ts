// Consent and permission (E4) — ported from the WPF app so behaviour is the
// same tool for tool: PermissionService (persisted allow_always/deny_always
// per tool::scope + a session cache), ToolDangerClassifier (same safe-list,
// same category table), ApprovalMode (manual|auto|skip), FolderScopeResolver
// (scope = the granted root containing the path, else the path; shell = cwd)
// and SensitiveReadGuard (a read outside the user's chosen folders asks).
// No 'electron' import: unit-tested in plain Node.

import fs from "node:fs";
import path from "node:path";

export type ToolCategory = "FileSystem" | "Browser" | "Clipboard" | "Shell" | "Screen" | "Network";
export type PermissionLevel = "Ask" | "AllowOnce" | "AllowAlways" | "Deny";
export type PermissionDecision = "Auto" | "NeedsConsent" | "Denied";
export type ApprovalMode = "manual" | "auto" | "skip";

export interface ToolMeta {
  name: string;
  category: ToolCategory;
  defaultPermission: PermissionLevel;
}

// ── ToolDangerClassifier (verbatim table) ──────────────────────────────────
const SAFE = new Set([
  "read_file", "read_document", "list_directory", "search_files", "read_clipboard",
  "web_search", "web_fetch", "run_subagent", "update_plan", "use_skill", "remember", "search_past_conversations",
  "computer_zoom", "get_computer_state", "read_screen_elements",
  "browser_read_page", "browser_find", "browser_list_tabs", "browser_screenshot",
]);
export function classifyDanger(name: string, category: ToolCategory): { isDangerous: boolean; reasonKey: string } {
  if (SAFE.has(name.toLowerCase())) return { isDangerous: false, reasonKey: "" };
  if (name.toLowerCase() === "run_python") return { isDangerous: true, reasonKey: "danger.code" };
  switch (category) {
    case "Shell": return { isDangerous: true, reasonKey: "danger.shell" };
    case "Screen": return { isDangerous: true, reasonKey: "danger.computerUse" };
    case "Browser": return { isDangerous: true, reasonKey: "danger.browser" };
    case "Clipboard": return { isDangerous: true, reasonKey: "danger.clipboard" };
    case "FileSystem": return { isDangerous: true, reasonKey: "danger.fileWrite" };
    case "Network": return { isDangerous: true, reasonKey: "danger.connector" };
    default: return { isDangerous: true, reasonKey: "danger.generic" };
  }
}

/** The tool's category/permission when the sidecar does not say (MCP has no
 * such fields): the WPF registry's values, by name; unknown (remote MCP)
 * tools are Network/Ask — fail-safe, exactly as RemoteMcpTool. */
const KNOWN: Record<string, { category: ToolCategory; defaultPermission: PermissionLevel }> = {
  read_file: { category: "FileSystem", defaultPermission: "AllowAlways" },
  read_document: { category: "FileSystem", defaultPermission: "AllowAlways" },
  list_directory: { category: "FileSystem", defaultPermission: "AllowAlways" },
  search_files: { category: "FileSystem", defaultPermission: "AllowAlways" },
  write_file: { category: "FileSystem", defaultPermission: "Ask" },
  edit_file: { category: "FileSystem", defaultPermission: "Ask" },
  create_directory: { category: "FileSystem", defaultPermission: "Ask" },
  move_file: { category: "FileSystem", defaultPermission: "Ask" },
  read_clipboard: { category: "Clipboard", defaultPermission: "AllowOnce" },
  write_clipboard: { category: "Clipboard", defaultPermission: "Ask" },
  run_command: { category: "Shell", defaultPermission: "Ask" },
  update_plan: { category: "Network", defaultPermission: "AllowAlways" },
  remember: { category: "Network", defaultPermission: "AllowAlways" },
  web_search: { category: "Network", defaultPermission: "AllowAlways" },
  search_past_conversations: { category: "Network", defaultPermission: "AllowAlways" },
  web_fetch: { category: "Network", defaultPermission: "AllowAlways" },
  use_skill: { category: "Network", defaultPermission: "AllowAlways" },
  computer_screenshot: { category: "Screen", defaultPermission: "Ask" },
  computer_click: { category: "Screen", defaultPermission: "Ask" },
  computer_type: { category: "Screen", defaultPermission: "Ask" },
  computer_key: { category: "Screen", defaultPermission: "Ask" },
  computer_scroll: { category: "Screen", defaultPermission: "Ask" },
  computer_drag: { category: "Screen", defaultPermission: "Ask" },
  computer_zoom: { category: "Screen", defaultPermission: "AllowAlways" },
  open_application: { category: "Screen", defaultPermission: "Ask" },
  browser_list_tabs: { category: "Browser", defaultPermission: "AllowAlways" },
  browser_tabs: { category: "Browser", defaultPermission: "Ask" },
  browser_navigate: { category: "Browser", defaultPermission: "Ask" },
  browser_read_page: { category: "Browser", defaultPermission: "AllowAlways" },
  browser_find: { category: "Browser", defaultPermission: "AllowAlways" },
  browser_click: { category: "Browser", defaultPermission: "Ask" },
  browser_type: { category: "Browser", defaultPermission: "Ask" },
  browser_screenshot: { category: "Browser", defaultPermission: "AllowAlways" },
  open_url: { category: "Browser", defaultPermission: "Ask" },
  run_python: { category: "Network", defaultPermission: "Ask" },
  run_subagent: { category: "Network", defaultPermission: "AllowAlways" },
};
export function toolMeta(name: string): ToolMeta {
  const k = KNOWN[name];
  return k ? { name, ...k } : { name, category: "Network", defaultPermission: "Ask" };
}

// ── ApprovalMode ───────────────────────────────────────────────────────────
export function parseApprovalMode(raw: string | undefined | null): ApprovalMode {
  const v = (raw ?? "").trim().toLowerCase();
  return v === "manual" || v === "skip" ? v : "auto";
}
/** ChatOrchestrator.ApplyApprovalMode, verbatim semantics. */
export function applyApprovalMode(decision: PermissionDecision, tool: ToolMeta, mode: ApprovalMode): PermissionDecision {
  if (decision === "Denied") return decision;
  const dangerous = classifyDanger(tool.name, tool.category).isDangerous;
  switch (mode) {
    case "manual": return dangerous || decision === "NeedsConsent" ? "NeedsConsent" : decision;
    case "skip": return "Auto";
    default: return decision === "NeedsConsent" && !dangerous ? "Auto" : decision;
  }
}

// ── PermissionService: persisted + session decisions ───────────────────────
export class PermissionStore {
  private persisted = new Map<string, string>();
  private readonly session = new Map<string, PermissionLevel>();
  constructor(private readonly filePath: string) {
    try {
      if (fs.existsSync(filePath)) this.persisted = new Map(Object.entries(JSON.parse(fs.readFileSync(filePath, "utf8")) as Record<string, string>));
    } catch {
      this.persisted = new Map();
    }
  }
  private key(tool: string, scope: string): string {
    return `${tool}::${scope}`;
  }
  check(tool: ToolMeta, scope: string): PermissionDecision {
    const p = this.persisted.get(this.key(tool.name, scope));
    if (p === "allow_always") return "Auto";
    if (p === "deny_always") return "Denied";
    const s = this.session.get(this.key(tool.name, scope));
    if (s === "AllowOnce" || s === "AllowAlways") return "Auto";
    if (s === "Deny") return "Denied";
    switch (tool.defaultPermission) {
      case "AllowAlways": return "Auto";
      case "Deny": return "Denied";
      default: return "NeedsConsent";
    }
  }
  hasStoredDecision(tool: string, scope: string): boolean {
    const p = this.persisted.get(this.key(tool, scope));
    return p === "allow_always" || p === "deny_always" || this.session.has(this.key(tool, scope));
  }
  recordSession(tool: string, scope: string, level: PermissionLevel): void {
    this.session.set(this.key(tool, scope), level);
  }
  persist(tool: string, scope: string, permission: "allow_always" | "deny_always"): void {
    this.persisted.set(this.key(tool, scope), permission);
    this.save();
  }
  /** FIX6: remove a persisted grant (used by sites.revoke — a control-plane op).
   * Also drops any matching session grant so the next action re-prompts. */
  revoke(tool: string, scope: string): boolean {
    const k = this.key(tool, scope);
    const had = this.persisted.delete(k);
    this.session.delete(k);
    if (had) this.save();
    return had;
  }
  /** FIX6: the raw persisted keys / values, for the approved-site VIEW. */
  persistedKeys(): string[] {
    return [...this.persisted.keys()];
  }
  persistedValue(key: string): string | undefined {
    return this.persisted.get(key);
  }
  private save(): void {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, JSON.stringify(Object.fromEntries(this.persisted), null, 2));
  }
  clearSession(): void {
    this.session.clear();
  }
}

// ── scope + sensitive-read (need the grant list) ───────────────────────────
function norm(p: string): string {
  return path.win32.normalize(p).replace(/[\\/]+$/, "").toLowerCase();
}
export function isWithin(fullPath: string, root: string): boolean {
  const a = norm(fullPath);
  const b = norm(root);
  return a === b || a.startsWith(b + "\\") || a.startsWith(b + "/");
}
export function containingRoot(fullPath: string, grants: readonly string[]): string | null {
  // Most specific granted root first — a project folder wins over a broad root.
  const sorted = [...grants].sort((x, y) => y.length - x.length);
  for (const g of sorted) if (isWithin(fullPath, g)) return g;
  return null;
}
function readPathArg(args: Record<string, unknown>, names: string[]): string | null {
  for (const n of names) {
    const v = args[n];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return null;
}
/** FIX26 Part 3: a relative path resolves INTO the first granted folder —
 * the same rule the sidecar's PathGuard applies — so the consent scope and the
 * tool agree. Only when nothing is granted does the profile remain the base
 * (the sidecar will refuse such a path anyway). */
function resolveFull(raw: string, home: string, grants: readonly string[] = []): string {
  const base = grants[0] ?? home;
  return path.win32.isAbsolute(raw) ? path.win32.normalize(raw) : path.win32.join(base, raw);
}
/** FolderScopeResolver.Resolve — the consent SCOPE key. */
export function resolveScope(tool: ToolMeta, args: Record<string, unknown>, grants: readonly string[], home: string, cwd: string | null): string {
  if (tool.category === "FileSystem") {
    const raw = readPathArg(args, ["path", "destination", "directory"]);
    if (!raw) return "*";
    const full = resolveFull(raw, home, grants);
    return containingRoot(full, grants) ?? full;
  }
  if (tool.category === "Shell") {
    // FIX26 Part 3: no working_directory and no retained cwd → the first
    // granted folder (the sidecar's default), not the profile.
    const wd = readPathArg(args, ["working_directory"]) ?? cwd ?? grants[0] ?? home;
    const full = resolveFull(wd, home, grants);
    return containingRoot(full, grants) ?? full;
  }
  return "*";
}
const READ_PATH_ARG: Record<string, string> = { read_file: "path", read_document: "path", list_directory: "path", search_files: "directory" };
/** SensitiveReadGuard.RequiresConsent — a silent read outside every granted
 * folder asks first (the built-in profile/temp roots do NOT count). */
export function readRequiresConsent(tool: ToolMeta, args: Record<string, unknown>, grants: readonly string[], home: string): boolean {
  const argName = READ_PATH_ARG[tool.name];
  if (!argName) return false;
  const raw = args[argName];
  if (typeof raw !== "string" || !raw) return false;
  const full = resolveFull(raw, home, grants);
  return containingRoot(full, grants) === null;
}

// ── the gateway's DLP marker ───────────────────────────────────────────────
export function dlpBlocked(args: Record<string, unknown>): string | null {
  if (args["_dlp_blocked"] === true) return typeof args["reason"] === "string" ? args["reason"] : "unspecified";
  return null;
}

export function summarizeArgs(args: Record<string, unknown>, max = 160): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (k.startsWith("_")) continue;
    const s = typeof v === "string" ? v : JSON.stringify(v);
    parts.push(`${k}=${s.length > 60 ? s.slice(0, 57) + "…" : s}`);
  }
  const joined = parts.join(", ");
  return joined.length > max ? joined.slice(0, max - 1) + "…" : joined;
}

// ─────────────────────────────────────────────────────────────────────────────
// FIX6 — per-site browser permissions (Claude-in-Chrome model, adopted exactly).
//
// Scope decision (§1.1): the grant unit is the ORIGIN (scheme://host[:port],
// default ports omitted), NOT the registrable domain. `docs.example.com` and
// `intranet.example.com` are the same registrable domain but different trust, and
// this project is containment-first, so a grant to one subdomain must not cover
// another. This is STRICTER than a registrable-domain reading of Anthropic's
// "site": `example.com` vs `www.example.com`, and http vs https, are distinct and
// each prompt once. A non-http(s) tab (chrome:, about:, file:) scopes by scheme.
//
// The site NEVER comes from a model argument (§1.2): for tab-acting tools the host
// resolves the target tab's LIVE url from the bridge (tabs.list); for navigate/
// open_url the destination is the url arg, which can only ever PROMPT for an
// unapproved origin — the model naming it cannot add it to the approved list.
// ─────────────────────────────────────────────────────────────────────────────

/** The fixed permission "tool" key for browser site grants: one grant covers
 * EVERY browser tool on that origin (Anthropic: "always allow actions on this
 * site"), so the store key is `browser_site::<origin>`, not per browser tool. */
export const BROWSER_SITE_PERM = "browser_site";

/** Browser tools gated per-site. Includes the content-read family (§1.3: reads
 * become per-site too — Anthropic gates reading a page). browser_list_tabs is the
 * ONE deliberate exception: it enumerates tabs to DISCOVER sites (bootstrap) and
 * returns only ids/titles/urls the user can already see, not page content. */
export const BROWSER_SITE_TOOLS = new Set([
  "browser_navigate", "browser_click", "browser_type", "browser_tabs", "open_url",
  "browser_read_page", "browser_find", "browser_screenshot",
]);
/** Of those, the ones whose target site is the LIVE tab (resolved from the bridge)
 * rather than a url argument. browser_navigate/open_url gate on their destination
 * url arg; browser_tabs (activate/new/close) is tab-management, gated on the live
 * target tab. */
export const BROWSER_TABACTING_TOOLS = new Set([
  "browser_click", "browser_type", "browser_read_page", "browser_find", "browser_screenshot", "browser_tabs",
]);

/** The origin scope for a url, or null if unparseable. http(s) → scheme://host[:port]
 * (default ports omitted, host lowercased — exactly URL.origin); other schemes →
 * "scheme:" so chrome:/about:/file: are distinct, promptable scopes. */
export function siteScope(rawUrl: string): string | null {
  try {
    const u = new URL(rawUrl);
    if (u.protocol === "http:" || u.protocol === "https:") return u.origin.toLowerCase();
    return u.protocol.toLowerCase();
  } catch {
    return null;
  }
}

/** The three categories that ALWAYS prompt, even on an always-allowed site
 * (downloads · entering potentially sensitive information · granting
 * authorizations). Best-effort recognisers from tool + args + destination; their
 * blind spots are documented in the report. Returns the category or null. */
export function alwaysPromptCategory(tool: string, args: Record<string, unknown>, targetUrl: string | null): "download" | "sensitive-info" | "authorization" | null {
  const url = (targetUrl ?? (typeof args["url"] === "string" ? (args["url"] as string) : "")).toLowerCase();
  // Granting authorizations — OAuth / consent / grant-access endpoints.
  if (/\/(oauth2?|o\/oauth2|authorize|consent|connect\/authorize|grant|sso\/saml)(\b|\/|\?)/.test(url) || /\baccounts\.google\.com\/o\/oauth2|login\.microsoftonline\.com\/.*\/authorize|github\.com\/login\/oauth\/authorize/.test(url)) {
    return "authorization";
  }
  // Downloading a file — a navigation/open to a downloadable asset.
  if ((tool === "browser_navigate" || tool === "open_url") && /\.(pdf|zip|exe|msi|dmg|pkg|xlsx?|docx?|pptx?|csv|tar|gz|7z|iso|bin|apk)(\?|#|$)/.test(url)) {
    return "download";
  }
  // Entering potentially sensitive information — typing a value that looks like a
  // card number, a government id, or a secret, into a page.
  if (tool === "browser_type") {
    const text = typeof args["text"] === "string" ? (args["text"] as string) : "";
    if (looksSensitive(text)) return "sensitive-info";
  }
  return null;
}

/** Very rough sensitive-value shapes (a card-like 13-19 digit run, an SSN, or a
 * long secret-like token). Deliberately conservative — the report states what it
 * cannot catch (a password the model types char-normally, sensitive info split
 * across fields, etc.). */
export function looksSensitive(text: string): boolean {
  const digits = text.replace(/[\s-]/g, "");
  if (/^\d{13,19}$/.test(digits)) return true;                  // card-like
  if (/^\d{3}-?\d{2}-?\d{4}$/.test(text.trim())) return true;   // US SSN shape
  if (/\b(sk|pk|ghp|xox[bp]|AKIA|AIza)[A-Za-z0-9_\-]{12,}\b/.test(text)) return true; // secret-like token
  return false;
}

/** The four ALWAYS-REFUSED rules (every mode, including skip): purchases ·
 * account creation · permanent deletions · following instructions found in web
 * content. The last is enforced as a RULE in the system prompt (data ≠
 * instruction), NOT here — this recogniser covers the other three, best-effort,
 * from destination url and action labels. Returns a reason or null. NOTE: text-
 * and label-based recognition is inherently incomplete; the report is explicit. */
export function alwaysRefused(tool: string, args: Record<string, unknown>, targetUrl: string | null, elementLabel: string | null): { reason: string } | null {
  const url = (targetUrl ?? (typeof args["url"] === "string" ? (args["url"] as string) : "")).toLowerCase();
  const label = (elementLabel ?? "").toLowerCase();
  const buy = /\b(place\s+order|buy\s+now|complete\s+purchase|confirm\s+(and\s+)?pay|pay\s+now|checkout|subscribe\s+and\s+pay)\b/;
  const acct = /\b(create\s+(an?\s+)?account|sign\s+up|register|create\s+profile)\b/;
  const del = /\b(delete\s+(account|permanently|forever)|permanently\s+delete|empty\s+trash|delete\s+forever)\b/;
  if (buy.test(label) || /\/(checkout|payment|place-?order)(\b|\/|\?)/.test(url)) return { reason: "making a purchase" };
  if (acct.test(label) || /\/(signup|sign-?up|register|create-?account)(\b|\/|\?)/.test(url)) return { reason: "creating an account" };
  if (del.test(label)) return { reason: "a permanent deletion" };
  return null;
}

/** The per-site decision for a browser tool, given the ALREADY-RESOLVED site
 * (resolved from the tab/destination by the caller — never from a model arg).
 * Pure, so the gate is unit-testable without a bridge. Order:
 *   1. always-refused (purchase/account/deletion) → blocked in every mode incl. skip;
 *   2. store decision for `browser_site::<site>` through the approval mode;
 *   3. always-prompt categories (download/sensitive-info/authorization) force a
 *      prompt even when the site is approved — but NOT in skip (skip = no checks). */
export interface BrowserSiteEval {
  permScope: string;
  refused: { reason: string } | null;
  alwaysPrompt: "download" | "sensitive-info" | "authorization" | null;
  decision: PermissionDecision;
}
export function evaluateBrowserSite(
  tool: string,
  args: Record<string, unknown>,
  site: string,
  store: PermissionStore,
  mode: ApprovalMode,
  targetUrl: string | null,
  elementLabel: string | null,
): BrowserSiteEval {
  const refused = alwaysRefused(tool, args, targetUrl, elementLabel);
  if (refused) return { permScope: site, refused, alwaysPrompt: null, decision: "Denied" };

  // The site grant is a single key covering every browser tool on this origin.
  const meta: ToolMeta = { name: BROWSER_SITE_PERM, category: "Browser", defaultPermission: "Ask" };
  let decision = applyApprovalMode(store.check(meta, site), meta, mode);

  const alwaysPrompt = mode === "skip" ? null : alwaysPromptCategory(tool, args, targetUrl);
  if (alwaysPrompt && decision === "Auto") decision = "NeedsConsent"; // still prompt on an approved site
  return { permScope: site, refused: null, alwaysPrompt, decision };
}

/** The approved-site list is a VIEW over the persisted permission store: every
 * key `browser_site::<origin>` == allow_always is an approved site. The page can
 * read this and revoke, but there is NO op to ADD by name (add happens only from
 * the consent handler), so the model/page can never approve a site. */
export function listApprovedSites(store: PermissionStore): string[] {
  return store.persistedKeys().filter((k) => k.startsWith(BROWSER_SITE_PERM + "::") && store.persistedValue(k) === "allow_always").map((k) => k.slice((BROWSER_SITE_PERM + "::").length)).sort();
}
