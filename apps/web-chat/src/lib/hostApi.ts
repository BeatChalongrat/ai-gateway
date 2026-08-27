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
}

export interface InstalledConnectorDto {
  id: string;
  isCustom: boolean;
  name: string | null;
  tools: string[];
  status: string;
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
  oauthConnect: (connectorId: string) => bridge.hostQuery("connectors.oauthConnect", { connectorId }),
  oauthDisconnect: (connectorId: string) => bridge.hostQuery("connectors.oauthDisconnect", { connectorId }),
  addCustomConnector: () => bridge.hostQuery<{ added: boolean }>("connectors.addCustomDialog", {}, 300_000),

  // settings (local subset)
  getSettings: async () => unwrap<SettingsSnapshot>(await bridge.hostQuery("settings.get")),
  setSetting: (key: SettingKey, value: unknown) =>
    bridge.hostQuery<{ key: string; restartRequired: boolean }>("settings.set", { key, value }),

  // FIX1: browser-extension folder escape hatch — the page names no path; this
  // opens a NATIVE folder picker in the host, which derives the id and authorises it.
  pickExtensionFolder: () =>
    bridge.hostQuery<{ folder: string; extensionId: string; connected: boolean; cancelled?: boolean }>("browser.pickExtensionFolder"),

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
  deleteGlobalNote: (noteId: string) => bridge.hostQuery("globalNotes.delete", { noteId }),
};
