// FIX47 Part D — the right-click menu matches what Claude Desktop shows on
// this machine (docs/fix47/d2/, measured through UI Automation), and never
// lists an action that cannot act.
import { describe, expect, it, vi } from "vitest";

import { actionFor, buildContextMenu, isOpenableLink, type ContextMenuHost, type ContextMenuParams } from "../src/main/context-menu";

const flags = (over: Partial<ContextMenuParams["editFlags"]> = {}) => ({
  canUndo: false, canRedo: false, canCut: false, canCopy: false, canPaste: false, canSelectAll: true, ...over,
});

function params(over: Partial<ContextMenuParams> = {}): ContextMenuParams {
  return { x: 10, y: 20, isEditable: false, selectionText: "", linkURL: "", mediaType: "none", editFlags: flags(), ...over };
}

const labels = (p: ContextMenuParams) =>
  buildContextMenu(p).map((i) => ("type" in i ? "—" : `${i.label}${i.enabled ? "" : " (off)"}`));

describe("right-click menu — what Claude Desktop shows", () => {
  it("response text with a selection: Copy · Select All", () => {
    expect(labels(params({ selectionText: "Hi! How can I help" }))).toEqual(["Copy", "—", "Select All"]);
  });

  it("response text without a selection: Copy is greyed, Select All works", () => {
    expect(labels(params())).toEqual(["Copy (off)", "—", "Select All"]);
  });

  it("empty composer: Cut and Copy greyed, Paste and Select All live, no Undo/Redo yet", () => {
    expect(labels(params({ isEditable: true, editFlags: flags({ canPaste: true }) }))).toEqual([
      "Cut (off)", "Copy (off)", "Paste", "—", "Select All",
    ]);
  });

  it("composer with selected text: Undo · Redo (off) · Cut · Copy · Paste · Select All", () => {
    const p = params({ isEditable: true, selectionText: "fix47 probe text", editFlags: flags({ canUndo: true, canCut: true, canCopy: true, canPaste: true }) });
    expect(labels(p)).toEqual(["Undo", "Redo (off)", "—", "Cut", "Copy", "Paste", "—", "Select All"]);
  });

  it("a link: Open Link in Default Browser · Copy Link Address · Copy (off) · Select All", () => {
    expect(labels(params({ linkURL: "https://example.com/x" }))).toEqual([
      "Open Link in Default Browser", "Copy Link Address", "—", "Copy (off)", "—", "Select All",
    ]);
  });

  it("a non-http link gets no open/copy-link entries (nothing that cannot act)", () => {
    expect(labels(params({ linkURL: "javascript:void(0)" }))).toEqual(["Copy (off)", "—", "Select All"]);
    expect(isOpenableLink("sran://x")).toBe(false);
    expect(isOpenableLink("https://a.b/c")).toBe(true);
  });

  it("an image adds Copy Image", () => {
    expect(labels(params({ mediaType: "image" }))[0]).toBe("Copy Image");
  });

  it("every entry names an accelerator the Edit menu also has, except link/image actions", () => {
    const p = params({ isEditable: true, selectionText: "x", editFlags: flags({ canUndo: true, canRedo: true, canCut: true, canCopy: true, canPaste: true }) });
    for (const i of buildContextMenu(p)) {
      if ("type" in i) continue;
      expect(i.accelerator, i.label).toMatch(/^Ctrl\+/);
    }
  });
});

describe("right-click menu — actions", () => {
  function host(): ContextMenuHost & Record<string, ReturnType<typeof vi.fn>> {
    return {
      copy: vi.fn(), cut: vi.fn(), paste: vi.fn(), undo: vi.fn(), redo: vi.fn(), selectAll: vi.fn(),
      copyImageAt: vi.fn(), writeClipboardText: vi.fn(), openExternal: vi.fn(),
    };
  }

  it("Open Link goes to the system browser with the clicked URL; Copy Link writes it", () => {
    const h = host();
    const p = params({ linkURL: "https://example.com/doc" });
    const items = buildContextMenu(p);
    actionFor(items[0], p, h);
    actionFor(items[1], p, h);
    expect(h.openExternal).toHaveBeenCalledWith("https://example.com/doc");
    expect(h.writeClipboardText).toHaveBeenCalledWith("https://example.com/doc");
  });

  it("Copy Image copies at the clicked point", () => {
    const h = host();
    const p = params({ mediaType: "image", x: 33, y: 44 });
    actionFor(buildContextMenu(p)[0], p, h);
    expect(h.copyImageAt).toHaveBeenCalledWith(33, 44);
  });

  it("the edit entries call the matching webContents verbs", () => {
    const h = host();
    const p = params({ isEditable: true, selectionText: "x", editFlags: flags({ canUndo: true, canRedo: true, canCut: true, canCopy: true, canPaste: true }) });
    for (const i of buildContextMenu(p)) actionFor(i, p, h);
    for (const verb of ["undo", "redo", "cut", "copy", "paste", "selectAll"]) expect(h[verb], verb).toHaveBeenCalledTimes(1);
  });
});
