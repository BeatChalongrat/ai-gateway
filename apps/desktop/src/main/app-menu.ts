// FIX33 B3 — the native application menu (File / Edit / View / Help) with
// accelerators. The menu bar itself stays auto-hidden (Alt shows it); the
// accelerators work regardless. Every item either drives the window
// (reload, zoom, always-on-top) or asks the page to do what it already does
// (new chat = the /chat route, settings = /chat/settings) — no new
// capability, no new bridge op. DevTools is NEVER in a release build.
//
// AM10 F — every label comes from the strings table in the current language,
// the role items included: a role with no label of its own is drawn with
// Electron's English word whatever the person chose. index.ts rebuilds the
// menu when the language changes.
//
// Pure: builds a MenuItemConstructorOptions-shaped template from callbacks;
// index.ts hands it to Menu.buildFromTemplate. No 'electron' import so the
// template is unit-testable.

import { t } from "./strings";

export interface MenuActions {
  newChat: () => void;
  openSettings: () => void;
  reload: () => void;
  toggleAlwaysOnTop: () => void;
  isAlwaysOnTop: () => boolean;
  openLogs: () => void;
  about: () => void;
  quit: () => void;
  /** Only wired when devtools are allowed (never in release). */
  toggleDevTools?: () => void;
}

/** A structural subset of Electron's MenuItemConstructorOptions. */
export interface MenuTemplateItem {
  /** What the item is, independent of its (translated) label. */
  id?: "devtools";
  label?: string;
  role?: string;
  type?: "normal" | "separator" | "checkbox";
  accelerator?: string;
  checked?: boolean;
  click?: () => void;
  submenu?: MenuTemplateItem[];
}

export function buildMenuTemplate(actions: MenuActions, opts: { isRelease: boolean }): MenuTemplateItem[] {
  const view: MenuTemplateItem[] = [
    { label: t("menu.reload"), accelerator: "CmdOrCtrl+R", click: actions.reload },
    { type: "separator" },
    { role: "resetZoom", label: t("menu.resetZoom"), accelerator: "CmdOrCtrl+0" },
    { role: "zoomIn", label: t("menu.zoomIn"), accelerator: "CmdOrCtrl+=" },
    { role: "zoomOut", label: t("menu.zoomOut"), accelerator: "CmdOrCtrl+-" },
    { type: "separator" },
    { label: t("menu.alwaysOnTop"), type: "checkbox", accelerator: "CmdOrCtrl+Shift+T", checked: actions.isAlwaysOnTop(), click: actions.toggleAlwaysOnTop },
    { role: "togglefullscreen", label: t("menu.fullscreen"), accelerator: "F11" },
  ];
  if (!opts.isRelease && actions.toggleDevTools) {
    view.push({ type: "separator" }, { id: "devtools", label: t("menu.devTools"), accelerator: "CmdOrCtrl+Shift+I", click: actions.toggleDevTools });
  }
  return [
    {
      label: t("menu.file"),
      submenu: [
        { label: t("menu.newChat"), accelerator: "CmdOrCtrl+N", click: actions.newChat },
        { label: t("menu.settings"), accelerator: "CmdOrCtrl+,", click: actions.openSettings },
        { type: "separator" },
        { label: t("menu.quit"), accelerator: "CmdOrCtrl+Q", click: actions.quit },
      ],
    },
    {
      label: t("menu.edit"),
      submenu: [
        { role: "undo", label: t("menu.undo") },
        { role: "redo", label: t("menu.redo") },
        { type: "separator" },
        { role: "cut", label: t("menu.cut") },
        { role: "copy", label: t("menu.copy") },
        { role: "paste", label: t("menu.paste") },
        { role: "selectAll", label: t("menu.selectAll") },
      ],
    },
    { label: t("menu.view"), submenu: view },
    {
      label: t("menu.help"),
      submenu: [
        { label: t("menu.openLogs"), click: actions.openLogs },
        { label: t("menu.about"), click: actions.about },
      ],
    },
  ];
}

/** Every accelerator in the template (for the pin test / docs). */
export function accelerators(items: MenuTemplateItem[]): string[] {
  const out: string[] = [];
  for (const it of items) {
    if (it.accelerator) out.push(it.accelerator);
    if (it.submenu) out.push(...accelerators(it.submenu));
  }
  return out;
}

/** True when any item (recursively) opens devtools — found by what it is, not by its label. */
export function hasDevTools(items: MenuTemplateItem[]): boolean {
  return items.some((it) => it.id === "devtools" || (it.submenu ? hasDevTools(it.submenu) : false));
}
