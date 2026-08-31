// FIX33 B3 — the native application menu (File / Edit / View / Help) with
// accelerators. The menu bar itself stays auto-hidden (Alt shows it); the
// accelerators work regardless. Every item either drives the window
// (reload, zoom, always-on-top) or asks the page to do what it already does
// (new chat = the /chat route, settings = /chat/settings) — no new
// capability, no new bridge op. DevTools is NEVER in a release build.
//
// Pure: builds a MenuItemConstructorOptions-shaped template from callbacks;
// index.ts hands it to Menu.buildFromTemplate. No 'electron' import so the
// template is unit-testable.

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
    { label: "Reload", accelerator: "CmdOrCtrl+R", click: actions.reload },
    { type: "separator" },
    { role: "resetZoom", accelerator: "CmdOrCtrl+0" },
    { role: "zoomIn", accelerator: "CmdOrCtrl+=" },
    { role: "zoomOut", accelerator: "CmdOrCtrl+-" },
    { type: "separator" },
    { label: "Always on Top", type: "checkbox", accelerator: "CmdOrCtrl+Shift+T", checked: actions.isAlwaysOnTop(), click: actions.toggleAlwaysOnTop },
    { role: "togglefullscreen", accelerator: "F11" },
  ];
  if (!opts.isRelease && actions.toggleDevTools) {
    view.push({ type: "separator" }, { label: "Toggle Developer Tools", accelerator: "CmdOrCtrl+Shift+I", click: actions.toggleDevTools });
  }
  return [
    {
      label: "File",
      submenu: [
        { label: "New Chat", accelerator: "CmdOrCtrl+N", click: actions.newChat },
        { label: "Settings", accelerator: "CmdOrCtrl+,", click: actions.openSettings },
        { type: "separator" },
        { label: "Quit", accelerator: "CmdOrCtrl+Q", click: actions.quit },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    { label: "View", submenu: view },
    {
      label: "Help",
      submenu: [
        { label: "Open Log Folder", click: actions.openLogs },
        { label: "About SRAN Desktop", click: actions.about },
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

/** True when any item (recursively) opens devtools. */
export function hasDevTools(items: MenuTemplateItem[]): boolean {
  return items.some((it) => (it.label ?? "").includes("Developer Tools") || (it.submenu ? hasDevTools(it.submenu) : false));
}
