// FIX76 — the docked side pane: the frame the canvas, workspace and file
// preview share. Pins the layout contract (FIX76 Part A):
//   * ≥ lg: a docked `complementary` landmark, no backdrop, no focus
//     trap, Esc closes only when focus is in the pane or on <body>;
//   * < lg: a slide-over `dialog` with a backdrop, aria-modal, a focus
//     trap and Esc always closing;
//   * one pane slot — opening another kind replaces the open one and
//     runs its closer (the canvas resets its own state);
//   * the docked width is published so floating pills and the credits
//     badge move off the pane;
//   * a message card opens the preview in the pane when a page hosts
//     the slot, and in the old modal when none does.
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFloatingPillOffset } from "@/hooks/useFloatingPillOffset";
import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";
import { useCanvasStore } from "@/stores/canvasStore";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import { useSidebarStore } from "@/stores/sidebarStore";

import { FilePreviewPane } from "@/components/chat/FilePreviewModal";
import { MessageAttachments } from "@/components/chat/MessageAttachments";
import { SidePane } from "./SidePane";

vi.mock("@/lib/api", () => ({
  api: {
    getFileText: vi.fn(async () => ({ text: "hello", extraction_status: "completed" })),
    fileObjectUrl: vi.fn(async () => "blob:x"),
    downloadFile: vi.fn(),
    getFilePreviewData: vi.fn(async () => { throw new Error("415"); }),
  },
  ApiError: class extends Error {},
}));

function setViewport(docked: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q === "(min-width: 1024px)" ? docked : q === "(min-width: 768px)",
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const noop = () => {};

function Pane({ open, onClose = noop }: { open: boolean; onClose?: () => void }) {
  return (
    <SidePane open={open} label="Canvas" maxWidth={672} closeLabel="Close canvas" onClose={onClose} testId="pane">
      <button type="button">inside</button>
    </SidePane>
  );
}

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  useSidePaneStore.setState({ pane: null, hostMounted: false, dockedWidth: 0 });
  useCanvasStore.setState({ open: false, artifact: null });
  useSidebarStore.setState({ collapsed: false });
  setViewport(true);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("docked (≥ lg)", () => {
  it("is a complementary landmark named after the panel, with no backdrop and no dialog role", () => {
    render(<Pane open />);
    const pane = screen.getByTestId("pane");
    expect(pane.getAttribute("role")).toBe("complementary");
    expect(pane.getAttribute("aria-label")).toBe("Canvas");
    expect(pane.getAttribute("aria-modal")).toBeNull();
    expect(pane.getAttribute("data-pane-mode")).toBe("docked");
    expect(screen.queryByTestId("pane-backdrop")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    // width: capped per panel, at most 45 % of the row
    expect(pane.style.width).toBe("min(672px, 45%)");
    // not fixed: it is a flex sibling, so the thread beside it stays live
    expect(pane.className).not.toContain("fixed");
  });

  it("renders nothing when closed (no hidden aside in the row)", () => {
    render(<Pane open={false} />);
    expect(screen.queryByTestId("pane")).toBeNull();
  });

  it("does not move focus into the pane when it opens (the composer keeps the caret)", () => {
    const outside = document.createElement("textarea");
    document.body.appendChild(outside);
    outside.focus();
    render(<Pane open />);
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("Esc closes only when focus is inside the pane or on the body", () => {
    const onClose = vi.fn();
    const outside = document.createElement("textarea");
    document.body.appendChild(outside);
    render(<Pane open onClose={onClose} />);

    outside.focus(); // typing in the composer
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    screen.getByText("inside").focus(); // reading the pane
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    (document.activeElement as HTMLElement).blur(); // nothing focused
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    outside.remove();
  });

  it("the close button closes", () => {
    const onClose = vi.fn();
    render(<Pane open onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Close canvas"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("overlay (< lg)", () => {
  beforeEach(() => setViewport(false));

  it("is a modal dialog with a backdrop; the backdrop click and Esc close it", () => {
    const onClose = vi.fn();
    render(<Pane open onClose={onClose} />);
    const pane = screen.getByTestId("pane");
    expect(pane.getAttribute("role")).toBe("dialog");
    expect(pane.getAttribute("aria-modal")).toBe("true");
    expect(pane.getAttribute("data-pane-mode")).toBe("overlay");
    expect(pane.className).toContain("fixed");
    fireEvent.click(screen.getByTestId("pane-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
    const outside = document.createElement("textarea");
    document.body.appendChild(outside);
    outside.focus();
    fireEvent.keyDown(window, { key: "Escape" }); // always, it is a dialog
    expect(onClose).toHaveBeenCalledTimes(2);
    outside.remove();
  });

  it("traps focus while open (moves it inside) and stays mounted, slid out, when closed", () => {
    const { rerender } = render(<Pane open />);
    expect(screen.getByTestId("pane").contains(document.activeElement)).toBe(true);
    rerender(<Pane open={false} />);
    const pane = screen.getByTestId("pane");
    expect(pane.className).toContain("translate-x-full");
    expect(pane.getAttribute("aria-hidden")).toBe("true");
  });

  it("publishes no docked width", () => {
    render(<Pane open />);
    expect(useSidePaneStore.getState().dockedWidth).toBe(0);
  });
});

describe("one pane slot (sidePaneStore)", () => {
  it("opening another kind replaces the open pane and runs its closer", () => {
    const closer = vi.fn();
    useSidePaneStore.getState().registerCloser("workspace", closer);
    act(() => useSidePaneStore.getState().open({ kind: "workspace" }));
    act(() => useSidePaneStore.getState().open({ kind: "canvas" }));
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "canvas" });
    expect(closer).toHaveBeenCalledTimes(1);
    // same kind again is not a replacement
    act(() => useSidePaneStore.getState().open({ kind: "canvas" }));
    expect(closer).toHaveBeenCalledTimes(1);
  });

  it("opening the canvas takes the slot; opening the workspace closes the canvas (its own state resets)", () => {
    act(() => useCanvasStore.getState().openCanvas({ type: "html", content: "<h1>x</h1>", title: "a.html" }));
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "canvas" });
    act(() => useSidePaneStore.getState().open({ kind: "workspace" }));
    expect(useCanvasStore.getState().open).toBe(false);
    expect(useCanvasStore.getState().artifact).toBeNull();
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "workspace" });
  });

  it("closing the canvas from its own button empties the slot; close() empties any pane", () => {
    act(() => useCanvasStore.getState().openCanvas({ type: "html", content: "<h1>x</h1>" }));
    act(() => useCanvasStore.getState().closeCanvas());
    expect(useSidePaneStore.getState().pane).toBeNull();
    act(() => useSidePaneStore.getState().open({ kind: "workspace" }));
    act(() => useSidePaneStore.getState().close());
    expect(useSidePaneStore.getState().pane).toBeNull();
    expect(useSidePaneStore.getState().dockedWidth).toBe(0);
  });
});

describe("floating pills move off a docked pane", () => {
  it("useFloatingPillOffset subtracts half the docked width", () => {
    const { result, rerender } = renderHook(() => useFloatingPillOffset());
    expect(result.current).toBe(130); // sidebar expanded, desktop
    act(() => useSidePaneStore.getState().setDockedWidth(400));
    rerender();
    expect(result.current).toBe(130 - 200);
    act(() => useSidebarStore.setState({ collapsed: true }));
    rerender();
    expect(result.current).toBe(-200);
  });
});

describe("file preview: pane when a page hosts the slot, modal otherwise", () => {
  const file: FileAttachment = { id: "f1", filename: "notes.txt", content_type: "text/plain", file_size_bytes: 12 } as FileAttachment;

  it("with a host: the card opens the docked preview pane, no modal, and the close button empties the slot", async () => {
    useSidePaneStore.setState({ hostMounted: true });
    render(
      <>
        <MessageAttachments files={[file]} />
        <FilePreviewPane />
      </>,
    );
    fireEvent.click(screen.getByText("notes.txt"));
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "preview", file });
    const pane = await screen.findByTestId("preview-pane");
    expect(pane.getAttribute("role")).toBe("complementary");
    expect(pane.getAttribute("aria-label")).toBe("notes.txt");
    expect(screen.queryByTestId("preview-backdrop")).toBeNull();
    expect(await screen.findByTestId("preview-text")).toHaveTextContent("hello");
    fireEvent.click(screen.getByTestId("preview-pane-close"));
    expect(useSidePaneStore.getState().pane).toBeNull();
  });

  it("without a host (quick-ask, share view): the old modal dialog opens instead", async () => {
    render(<MessageAttachments files={[file]} />);
    fireEvent.click(screen.getByText("notes.txt"));
    expect(useSidePaneStore.getState().pane).toBeNull();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByTestId("preview-backdrop")).toBeInTheDocument();
  });
});
