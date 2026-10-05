// AM10 F — the tray's context menu as a template, in the current language.
//
// It was built inline in index.ts (FIX33 B4 / FIX34 Part A) with English
// labels. The items are unchanged — Show / Hide, Quick ask with its hotkey,
// the always-on-top checkbox, Sign in / Sign out, Clear local data…, Quit —
// only the words now come from the strings table, so index.ts can rebuild
// the menu when the language changes (rebuildTrayMenu, the same call the
// hotkey and always-on-top changes already make).
//
// Pure (no 'electron' import): index.ts hands the template to
// Menu.buildFromTemplate.

import { describeAccelerator } from "./shell-prefs";
import { t } from "./strings";

export interface TrayActions {
  toggleMain: () => void;
  toggleQuick: () => void;
  setAlwaysOnTop: (on: boolean) => void;
  signIn: () => void;
  signOut: () => void;
  clearLocalData: () => void;
  quit: () => void;
}

/** A structural subset of Electron's MenuItemConstructorOptions. */
export interface TrayTemplateItem {
  label?: string;
  type?: "normal" | "separator" | "checkbox";
  checked?: boolean;
  click?: (item: { checked: boolean }) => void;
}

export function buildTrayTemplate(actions: TrayActions, state: { quickHotkey: string; alwaysOnTop: boolean }): TrayTemplateItem[] {
  return [
    { label: t("tray.showHide"), click: () => actions.toggleMain() },
    // The hotkey rides behind a tab, where a menu shows its accelerator.
    { label: `${t("tray.quickAsk")}\t${describeAccelerator(state.quickHotkey)}`, click: () => actions.toggleQuick() },
    { label: t("tray.alwaysOnTop"), type: "checkbox", checked: state.alwaysOnTop, click: (item) => actions.setAlwaysOnTop(item.checked) },
    { type: "separator" },
    { label: t("tray.signIn"), click: () => actions.signIn() },
    { label: t("tray.signOut"), click: () => actions.signOut() },
    { type: "separator" },
    // Local-data wipe (retention program): removes the plaintext derived
    // caches (conversation-state, run-ledger, logs). Everything is
    // re-derivable from the server; still confirmed first because it is a
    // deletion.
    { label: t("tray.clearLocalData"), click: () => actions.clearLocalData() },
    { type: "separator" },
    { label: t("tray.quit"), click: () => actions.quit() },
  ];
}
