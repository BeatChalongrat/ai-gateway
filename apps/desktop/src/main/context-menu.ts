// FIX47 Part D — the right-click menu.
//
// Until this file the shell had an Edit menu (app-menu.ts) and nothing on
// right-click: Electron shows no context menu unless the app builds one.
// The set below is what Claude Desktop 1.28929 shows on this machine,
// measured through UI Automation (docs/fix47/d2/):
//
//   response text, selection      Copy · Select All
//   response text, no selection   Copy (disabled) · Select All
//   composer, empty               Cut (off) · Copy (off) · Paste · Select All
//   composer, text selected       Undo · Redo (off) · Cut · Copy · Paste · Select All
//   link                          Open Link in Default Browser · Copy Link
//                                 Address · Copy (off) · Select All
//
// Disabled entries are kept where Claude keeps them (the standard edit set
// with its accelerators visible); nothing else that cannot act is listed.
// One addition beyond the measurement: "Copy Image" on an image, because
// it works (webContents.copyImageAt) — Claude was not measured on images.
//
// Pure template builder (no 'electron' import) so it is unit-tested in
// plain Node; the installer below wires it to a webContents.

export interface ContextMenuParams {
  readonly x: number;
  readonly y: number;
  readonly isEditable: boolean;
  readonly selectionText: string;
  readonly linkURL: string;
  readonly mediaType: string;
  /** FIX81 ④ — the image's source URL (data:/blob:/https:), for Save
   *  Image As. Empty when the target is not an image. */
  readonly srcURL?: string;
  readonly editFlags: {
    readonly canUndo: boolean;
    readonly canRedo: boolean;
    readonly canCut: boolean;
    readonly canCopy: boolean;
    readonly canPaste: boolean;
    readonly canSelectAll: boolean;
  };
}

export type ContextMenuItem =
  | { readonly type: "separator" }
  | {
      readonly id: "undo" | "redo" | "cut" | "copy" | "paste" | "selectAll" | "openLink" | "copyLink" | "copyImage" | "saveImage";
      readonly label: string;
      readonly accelerator?: string;
      readonly enabled: boolean;
    };

/** True for the http(s) links the system browser may open. */
export function isOpenableLink(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

export function buildContextMenu(p: ContextMenuParams): ContextMenuItem[] {
  const items: ContextMenuItem[] = [];
  const hasSelection = p.selectionText.trim().length > 0;

  if (p.linkURL && isOpenableLink(p.linkURL)) {
    items.push(
      { id: "openLink", label: "Open Link in Default Browser", enabled: true },
      { id: "copyLink", label: "Copy Link Address", enabled: true },
      { type: "separator" },
    );
  }
  if (p.mediaType === "image") {
    items.push(
      { id: "copyImage", label: "Copy Image", enabled: true },
      // FIX81 ④ — Save goes through webContents.downloadURL, which lands in
      // the SAME will-download policy FIX80 repaired (app-origin allowed →
      // native save dialog) — one download mechanism, not a second one.
      { id: "saveImage", label: "Save Image As…", enabled: Boolean(p.srcURL) },
      { type: "separator" },
    );
  }

  if (p.isEditable) {
    // The standard edit set, as Claude shows it: Undo/Redo appear once
    // there is history to undo; Cut/Copy follow the selection.
    if (p.editFlags.canUndo || p.editFlags.canRedo) {
      items.push(
        { id: "undo", label: "Undo", accelerator: "Ctrl+Z", enabled: p.editFlags.canUndo },
        { id: "redo", label: "Redo", accelerator: "Ctrl+Shift+Z", enabled: p.editFlags.canRedo },
        { type: "separator" },
      );
    }
    items.push(
      { id: "cut", label: "Cut", accelerator: "Ctrl+X", enabled: p.editFlags.canCut && hasSelection },
      { id: "copy", label: "Copy", accelerator: "Ctrl+C", enabled: p.editFlags.canCopy && hasSelection },
      { id: "paste", label: "Paste", accelerator: "Ctrl+V", enabled: p.editFlags.canPaste },
      { type: "separator" },
      // Chromium reports canSelectAll=false for an empty field; Claude keeps
      // the entry live there, and so does the app's Edit menu.
      { id: "selectAll", label: "Select All", accelerator: "Ctrl+A", enabled: true },
    );
    return items;
  }

  items.push(
    { id: "copy", label: "Copy", accelerator: "Ctrl+C", enabled: hasSelection },
    { type: "separator" },
    { id: "selectAll", label: "Select All", accelerator: "Ctrl+A", enabled: true },
  );
  return items;
}

/** The actions a host must provide for the menu to act. */
export interface ContextMenuHost {
  copy(): void;
  cut(): void;
  paste(): void;
  undo(): void;
  redo(): void;
  selectAll(): void;
  copyImageAt(x: number, y: number): void;
  writeClipboardText(text: string): void;
  openExternal(url: string): void;
  /** FIX81 ④ — start a download of the image's source URL (rides the
   *  will-download policy: app-origin → save dialog). */
  downloadURL(url: string): void;
}

/** What clicking an item does — separated from Electron for the tests. */
export function actionFor(item: ContextMenuItem, p: ContextMenuParams, host: ContextMenuHost): void {
  if (!("id" in item)) return;
  switch (item.id) {
    case "undo": return host.undo();
    case "redo": return host.redo();
    case "cut": return host.cut();
    case "copy": return host.copy();
    case "paste": return host.paste();
    case "selectAll": return host.selectAll();
    case "openLink": return host.openExternal(p.linkURL);
    case "copyLink": return host.writeClipboardText(p.linkURL);
    case "copyImage": return host.copyImageAt(p.x, p.y);
    case "saveImage": return p.srcURL ? host.downloadURL(p.srcURL) : undefined;
  }
}
