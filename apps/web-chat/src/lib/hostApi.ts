// Typed client for the Phase 5 desktop-only screens (SHELL_PHASE5_REPORT.md §2).
//
// Every call is a host.query over the bridge; the host validates the op against
// its closed enum and applies the refusal rules. The page NEVER sends a path, a
// command line, or a secret — the dangerous inputs (skill file, grant folder,
// api key, spawn) are collected by the host through native OS dialogs, so the
// functions that trigger those take no such argument here either.
//
// DTO field names are camelCase to match the host's JsonSerializerDefaults.Web.

import { bridge, type HostResult } from "./bridge";

export interface SkillDto {
  id: string;
  name: string;
  description: string;
  triggers: string[];
  isBuiltin: boolean;
  isEnabled: boolean;
}

export interface TaskDto {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  isEnabled: boolean;
  nextRunAt: string | null;
  lastRunAt: string | null;
  lastResult: string | null;
  permissionMode: string;     // FIX8 A1.3 "manual" | "auto" | "skip"
  staggerSeconds: number;     // FIX8 A1.5 deterministic per-task offset
}

/** FIX8 A1.2/A1.4 — one line of a task's run history. */
export interface TaskRunDto {
  id: number;
  ranAt: string;
  scheduledFor: string | null;
  outcome: string;            // "ok" | "error" | "skipped" | "catchup"
  reason: string | null;      // "asleep" | "prev_running" | "others_running" | null
  result: string | null;
}

export interface CatalogConnectorDto {
  id: string;
  name: string;
  description: string;
  category: string;
  authType: "none" | "api_key" | "oauth2";
  tools: string[];
  installed: boolean;
  status: string;
  /** FIX69: true while the catalogue is the embedded starter one, whose server
   * URLs are placeholders — a key-based card then has no destination and is
   * not shown. Absent on an older host. */
  placeholder?: boolean;
}

/** FIX31 Part 2: what the page may know about an oauth2 connector — never a token. */
export interface ConnectorOAuthStatusDto {
  connectorId: string;
  configured: boolean;
  enabled: boolean;
  connected: boolean;
  expiresAt: number | null;
  scopes: string[];
  inFlight: boolean;
}

export interface InstalledConnectorDto {
  id: string;
  isCustom: boolean;
  name: string | null;
  tools: string[];
  status: string;
  /** FIX69: THAT a key is stored — never the key. Absent on an older host. */
  hasKey?: boolean;
  /** FIX69: "stdio" | "http" for a custom connector; absent otherwise. */
  transport?: string | null;
  /** FIX69: the remote server's URL for a custom http connector (the user's
   * own input; carries no key). */
  serverUrl?: string | null;
}

/** FIX1: live browser-bridge status + the folder the user should load. */
export interface BrowserStatus {
  connected: boolean;
  extensionFolder: string;
  extensionId: string;
  hostName: string;
}

export interface SettingsSnapshot {
  theme: string;
  minimizeToTray: boolean;
  startWithWindows: boolean;
  crashReportingEnabled: boolean;
  syncAgentConversations: boolean;
  preferLocalOfficeGeneration: boolean;
  defaultModel: string;
  chatEdgeBaseUrl: string;
  gatewayBaseUrl: string;
  browserExtensionId: string;
  writingStyleProfile: string;
  locale: string;
  grantedFolders: string[];       // FIX25 §3: the host overlays its GrantStore list (the sidecar's field was stale)
  approvalMode?: string;          // FIX25 §1: "manual" | "auto" | "skip" — absent on a pre-FIX25 sidecar
  browser?: BrowserStatus; // FIX1: present on the desktop host; absent pre-FIX1
}

export type SettingKey =
  | "theme" | "minimizeToTray" | "startWithWindows" | "crashReportingEnabled"
  | "syncAgentConversations" | "preferLocalOfficeGeneration" | "defaultModel"
  | "chatEdgeBaseUrl" | "gatewayBaseUrl" | "browserExtensionId" | "writingStyleProfile" | "locale"
  | "approvalMode"; // FIX25 §1 — the composer's permission-mode selector (same closed op; page-only, never a tool)

/** FIX33 — the context meter's numbers (host event `context_budget` and op `context.info`;
 *  the web path's GET /context-info maps onto the same fields). */
export interface ContextBudgetDto {
  used_ratio: number;
  history_tokens: number;
  messages_budget: number;
  threshold_tokens: number;
  messages_total: number;
  messages_sent: number;
  state_present: boolean;
  compacted: boolean;
  dropped: number;
  est_messages_remaining: number;
  show_meter: boolean;
  meter_show_ratio: number;
}

export interface GlobalNoteDto {
  id: string;
  content: string;
  createdAt: string;
}

/** Unwrap a HostResult, throwing on refusal so screen code can try/catch. */
function unwrap<T>(r: HostResult<T>): T {
  if (!r.ok) throw new HostError(r.error ?? "unknown");
  return r.data as T;
}

/** FIX34 Part A — the host-owned shell preferences (shell.prefs.get/set). */
export interface ShellPrefsDto {
  /** Electron accelerator for the quick-ask window (e.g. "CommandOrControl+Shift+A"). */
  quickHotkey: string;
  /** What a single click on the tray icon opens (§2.5: default "main"). */
  trayClick: "main" | "quick";
}

export class HostError extends Error {
  constructor(public readonly code: string) {
    super(`host refused: ${code}`);
    this.name = "HostError";
  }
}

export const hostApi = {
  // skills
  listSkills: async () => unwrap<{ skills: SkillDto[] }>(await bridge.hostQuery("skills.list")).skills,
  setSkillEnabled: (skillId: string, enabled: boolean) =>
    bridge.hostQuery("skills.setEnabled", { skillId, enabled }),
  installSkill: () => bridge.hostQuery<{ installed: boolean; name?: string; cancelled?: boolean }>("skills.installPicker"),
  /** FIX28 §3 (G3): write a skill from a form — no .zip/.skill archive. */
  createSkill: (input: { name: string; displayName?: string; description?: string; triggers?: string[]; instructions: string }) =>
    bridge.hostQuery<{ created: boolean; skillId: string }>("skills.create", input),
  deleteSkill: (skillId: string) => bridge.hostQuery("skills.delete", { skillId }),

  // scheduled tasks
  listTasks: async () => unwrap<{ tasks: TaskDto[] }>(await bridge.hostQuery("tasks.list")).tasks,
  createTask: (name: string, prompt: string, cron: string, permissionMode: string) =>
    bridge.hostQuery<TaskDto>("tasks.create", { name, prompt, cron, permissionMode }),
  updateTask: (taskId: string, name: string, prompt: string, cron: string, permissionMode: string) =>
    bridge.hostQuery<TaskDto>("tasks.update", { taskId, name, prompt, cron, permissionMode }),
  taskRuns: async (taskId: string) => unwrap<{ runs: TaskRunDto[] }>(await bridge.hostQuery("tasks.runs", { taskId })).runs,
  setTaskEnabled: (taskId: string, enabled: boolean) =>
    bridge.hostQuery<TaskDto>("tasks.setEnabled", { taskId, enabled }),
  deleteTask: (taskId: string) => bridge.hostQuery("tasks.delete", { taskId }),
  runTask: (taskId: string) => bridge.hostQuery<TaskDto>("tasks.runNow", { taskId }, 120_000),

  // connectors
  listConnectorCatalog: async () =>
    unwrap<{ connectors: CatalogConnectorDto[] }>(await bridge.hostQuery("connectors.listCatalog")).connectors,
  listInstalledConnectors: async () =>
    unwrap<{ connectors: InstalledConnectorDto[] }>(await bridge.hostQuery("connectors.listInstalled")).connectors,
  installConnector: (connectorId: string) =>
    bridge.hostQuery<{ installed: boolean; cancelled?: boolean }>("connectors.install", { connectorId }, 60_000),
  uninstallConnector: (connectorId: string) => bridge.hostQuery("connectors.uninstall", { connectorId }),
  // The sign-in happens in the SYSTEM browser and can take a while (the user
  // may read the provider's consent screen): a long timeout, host-bounded at 10 min.
  oauthConnect: (connectorId: string) => bridge.hostQuery<{ connected: boolean; expiresAt: number | null }>("connectors.oauthConnect", { connectorId }, 11 * 60_000),
  oauthDisconnect: (connectorId: string) => bridge.hostQuery<{ connected: boolean; revoked: boolean }>("connectors.oauthDisconnect", { connectorId }, 60_000),
  oauthStatus: async (connectorIds: string[]) =>
    unwrap<{ statuses: ConnectorOAuthStatusDto[] }>(await bridge.hostQuery("connectors.oauthStatus", { connectorIds })).statuses,
  addCustomConnector: () => bridge.hostQuery<{ added: boolean }>("connectors.addCustomDialog", {}, 300_000),
  /** FIX69: a remote (https) MCP server. The host opens ITS OWN form for the
   * url and the optional key; the page sends nothing and learns only
   * added / cancelled / a reason code (remote-refused:*, test-failed:*). */
  addRemoteConnector: () => bridge.hostQuery<{ added: boolean }>("connectors.addRemoteDialog", {}, 300_000),

  // FIX34 Part A — shell prefs + the quick-ask window hand-offs. A refused
  // hotkey comes back as { ok:false, error:"hotkey-taken" | "hotkey-<reason>" }
  // with the UNCHANGED prefs in data; the page shows the reason.
  getShellPrefs: async () => unwrap<ShellPrefsDto>(await bridge.hostQuery("shell.prefs.get")),
  setShellPrefs: (patch: Partial<ShellPrefsDto>) => bridge.hostQuery<ShellPrefsDto>("shell.prefs.set", patch),
  quickClose: () => bridge.hostQuery<{ closed: boolean }>("quick.close", {}, 5_000),
  quickOpenInMain: (conversationId: string) => bridge.hostQuery<{ opened: boolean }>("quick.openInMain", { conversationId }, 10_000),

  // settings (local subset)
  getSettings: async () => unwrap<SettingsSnapshot>(await bridge.hostQuery("settings.get")),
  setSetting: (key: SettingKey, value: unknown) =>
    bridge.hostQuery<{ key: string; restartRequired: boolean }>("settings.set", { key, value }),

  // FIX1: browser-extension folder escape hatch — the page names no path; this
  // opens a NATIVE folder picker in the host, which derives the id and authorises it.
  pickExtensionFolder: () =>
    bridge.hostQuery<{ folder: string; extensionId: string; connected: boolean; cancelled?: boolean }>("browser.pickExtensionFolder"),
  /** FIX70: mint a fresh pairing token. The old one stops working at once; the
   * app writes the new one into the extension folder itself, so nothing is
   * pasted. The page learns only that it happened — never a token. */
  rotatePairingToken: () =>
    bridge.hostQuery<{ rotated: boolean; provisioned: boolean; connected: boolean }>("browser.rotatePairingToken"),

  // folder grants — the page never names a path; grant opens the native picker
  listFolders: async () => unwrap<{ folders: string[] }>(await bridge.hostQuery("folders.list")).folders,
  pickAndGrantFolder: () =>
    bridge.hostQuery<{ granted: boolean; folders?: string[]; cancelled?: boolean; duplicate?: boolean }>("folders.pickAndGrant"),
  revokeFolder: (folder: string) => bridge.hostQuery<{ revoked: boolean; folders: string[] }>("folders.revoke", { folder }),

  // FIX6: approved sites (Claude-in-Chrome "Your approved sites"). The page can
  // LIST and REVOKE; there is deliberately no add — a site is approved only
  // through the consent prompt, so the page can never name a site to approve.
  listSites: async () => unwrap<{ sites: string[] }>(await bridge.hostQuery("sites.list")).sites,
  revokeSite: (origin: string) => bridge.hostQuery<{ revoked: boolean; sites: string[] }>("sites.revoke", { origin }),

  // local global agent notes
  listGlobalNotes: async () => unwrap<{ notes: GlobalNoteDto[] }>(await bridge.hostQuery("globalNotes.list")).notes,
  /** FIX33 A-D5 — the context meter's numbers for one conversation (same shape as the host's context_budget event). */
  contextInfo: async (conversationId: string) => unwrap<ContextBudgetDto>(await bridge.hostQuery("context.info", { conversationId })),
  /** FIX33 A-D5 — "summarise and continue" with the user's instructions (host summarises, sets the boundary). */
  compactContext: async (conversationId: string, instructions: string) =>
    unwrap<{ ok: boolean }>(await bridge.hostQuery("context.compact", { conversationId, instructions }, 120_000)),
  deleteGlobalNote: (noteId: string) => bridge.hostQuery("globalNotes.delete", { noteId }),
  /** FIX127 C3 — the new-chat page abandons the conversation it created for
   *  an attachment. Fire-and-forget on pagehide: the request crosses the IPC
   *  before the renderer goes, and the host deletes only a 0-message chat. */
  discardEmptyConversation: (conversationId: string) => {
    void bridge.hostQuery("conversation.discardEmpty", { conversationId }).catch(() => {});
  },
};
