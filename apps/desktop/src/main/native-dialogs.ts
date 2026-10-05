// AM10 F — what the shell's dialogs, notifications and the OAuth loopback
// page say, built from the strings table in the current language.
//
// index.ts used to write these sentences inline, in English, beside the
// dialog.showMessageBox / new Notification call that showed them. Each one
// is now a small pure builder here: index.ts (and connector-oauth.ts for the
// loopback page, turn-coordinator.ts for the consent texts) hands the result
// to Electron. The button ORDER never changes with the language — the caller
// reads the answer by index (defaultId / cancelId travel with the spec).
//
// What the caller passes in — a path, a version, a host, a reason code — is
// data and is shown as it is; only our own words are translated.
//
// No 'electron' import: unit-tested in plain Node.

import { currentLocale, t } from "./strings";

/** The app's name is its identity, the same in every language. */
const APP_NAME = "SRAN Desktop";

/** A structural subset of Electron's MessageBoxOptions. */
export interface MessageBoxSpec {
  type: "info" | "warning" | "error" | "question";
  title: string;
  message: string;
  detail?: string;
  buttons: string[];
  defaultId?: number;
  cancelId?: number;
  noLink?: boolean;
}

export interface NotificationSpec {
  title: string;
  body: string;
}

// ── Help → About ─────────────────────────────────────────────────────────────
export function aboutDialog(v: { version: string; electron: string; configPath: string }): MessageBoxSpec {
  return {
    type: "info",
    title: t("dialog.about.title"),
    message: t("dialog.about.message", { version: v.version }),
    detail: t("dialog.about.detail", { electron: v.electron, configPath: v.configPath }),
    buttons: [t("dialog.ok")],
  };
}

// ── the renderer died twice ─────────────────────────────────────────────────
/** Buttons: 0 = reload, 1 = quit. */
export function rendererGoneDialog(v: { reason: string; exitCode: number; logPath: string | null }): MessageBoxSpec {
  return {
    type: "error",
    title: APP_NAME,
    message: t("dialog.rendererGone.message"),
    detail: t("dialog.rendererGone.detail", { reason: v.reason, exitCode: v.exitCode, log: v.logPath ?? t("dialog.rendererGone.noLog") }),
    buttons: [t("dialog.rendererGone.reload"), t("dialog.rendererGone.quit")],
    defaultId: 0,
    cancelId: 1,
  };
}

// ── tray → Clear local data… ────────────────────────────────────────────────
/** Buttons: 0 = clear, 1 = cancel. */
export function clearLocalDataDialog(): MessageBoxSpec {
  return {
    type: "warning",
    title: t("dialog.clearData.title"),
    message: t("dialog.clearData.message"),
    detail: t("dialog.clearData.detail"),
    buttons: [t("dialog.clearData.confirm"), t("dialog.cancel")],
    defaultId: 1,
    cancelId: 1,
  };
}

// ── MCP connector consent (FIX2 / FIX69) ────────────────────────────────────
/** The spawn consent: names the exact command line the sidecar would run. */
export function spawnConsentMessage(name: string, commandLine: string): string {
  return t("consent.spawn", { name, command: commandLine });
}

/** The remote consent: names the exact origin and whether a key is sent (and
 *  as which header — "Authorization: Bearer" when none was chosen). */
export function remoteConsentMessage(host: string, origin: string, keyed: boolean, header: string): string {
  const key = keyed ? t("consent.remote.keyYes", { header: header || "Authorization: Bearer" }) : t("consent.remote.keyNone");
  return t("consent.remote", { host, origin, key });
}

/** Buttons: 0 = cancel, 1 = add. */
export function confirmConnectorDialog(kind: "local" | "remote", message: string): MessageBoxSpec {
  return {
    type: "warning",
    title: kind === "local" ? t("dialog.connector.localTitle") : t("dialog.connector.remoteTitle"),
    message,
    buttons: [t("dialog.cancel"), t("dialog.connector.add")],
    defaultId: 0,
    cancelId: 0,
  };
}

// ── "Ask SRAN about this file" (FIX34 Part C) ───────────────────────────────
export function attachSignInFirstDialog(): MessageBoxSpec {
  return { type: "info", title: APP_NAME, message: t("dialog.attach.signInFirst"), buttons: [t("dialog.ok")] };
}

export function attachNotOpenedDialog(filePath: string): MessageBoxSpec {
  return { type: "error", title: APP_NAME, message: t("dialog.attach.notOpened"), detail: filePath, buttons: [t("dialog.ok")] };
}

export function attachTooLargeDialog(filePath: string): MessageBoxSpec {
  return { type: "error", title: APP_NAME, message: t("dialog.attach.tooLarge"), detail: filePath, buttons: [t("dialog.ok")] };
}

/** Buttons: 0 = cancel, 1 = attach (allowing the folder first when it is not granted). */
export function attachConfirmDialog(v: { filePath: string; folder: string; inGrant: boolean }): MessageBoxSpec {
  return {
    type: "question",
    title: t("dialog.attach.title"),
    message: t("dialog.attach.message", { file: v.filePath }),
    detail: v.inGrant ? t("dialog.attach.detailInGrant") : t("dialog.attach.detailNewFolder", { folder: v.folder }),
    buttons: [t("dialog.cancel"), v.inGrant ? t("dialog.attach.attach") : t("dialog.attach.allowAndAttach")],
    defaultId: 1,
    cancelId: 0,
    noLink: true,
  };
}

export function attachFolderRefusedDialog(folder: string, verdict: string): MessageBoxSpec {
  // The validator's verdict (a code such as SystemFolder) rides under the path as it is.
  const detail = [folder, `(${verdict})`].join("\n");
  return { type: "error", title: APP_NAME, message: t("dialog.attach.folderRefused"), detail, buttons: [t("dialog.ok")] };
}

export function attachChatFailedDialog(): MessageBoxSpec {
  return { type: "error", title: APP_NAME, message: t("dialog.attach.chatFailed"), buttons: [t("dialog.ok")] };
}

export function attachFailedDialog(fileName: string): MessageBoxSpec {
  return { type: "error", title: APP_NAME, message: t("dialog.attach.failed"), detail: fileName, buttons: [t("dialog.ok")] };
}

// ── OS notifications ────────────────────────────────────────────────────────
/** FIX8 A1.1 — a missed scheduled run is being caught up. */
export function catchUpNotification(task: { name: string; catchUpFor: string; skippedOlder: number }): NotificationSpec {
  const when = new Date(task.catchUpFor).toLocaleString(currentLocale() === "th" ? "th-TH" : undefined);
  const skipped = task.skippedOlder > 0 ? t("notify.catchUp.skipped", { count: task.skippedOlder }) : "";
  return { title: t("notify.catchUp.title"), body: t("notify.catchUp.body", { name: task.name, when, skipped }) };
}

export function dataClearedNotification(fileCount: number): NotificationSpec {
  return { title: APP_NAME, body: t("notify.dataCleared.body", { count: fileCount }) };
}

/** A global shortcut another app holds. `accelerator` is already in its human
 *  spelling (shell-prefs describeAccelerator). */
export function hotkeyUnavailableNotification(which: "main" | "quick", accelerator: string): NotificationSpec {
  const what = which === "main" ? t("notify.hotkey.mainWindow") : t("notify.hotkey.quickWindow");
  return { title: t("notify.hotkey.title"), body: t("notify.hotkey.body", { accelerator, what }) };
}

// ── the OAuth loopback page (connector-oauth.ts) ────────────────────────────
export type OAuthCallbackKind = "connected" | "notConnected" | "incomplete" | "mismatch";

/** The page the system browser shows when a connector sign-in comes back to
 *  the loopback listener. Static text from the table; nothing from the
 *  request is written into it. */
export function oauthCallbackPage(kind: OAuthCallbackKind): string {
  const title = t(`oauth.${kind}.title`);
  const body = t(`oauth.${kind}.body`);
  return (
    `<!doctype html><html lang="${currentLocale()}"><head><meta charset="utf-8"><title>${title}</title>` +
    `<style>body{font-family:system-ui,sans-serif;margin:3rem auto;max-width:32rem;color:#222}</style></head>` +
    `<body><h2>${title}</h2><p>${body}</p></body></html>`
  );
}
