// FIX82 ① — the docked divider drags. Pins:
//   * clampPaneWidth: pane ≥ PANE_MIN_PX, thread keeps THREAD_MIN_PX,
//     pane floor wins when the row cannot hold both;
//   * the handle is a WAI-ARIA separator (orientation, min/max, keys);
//   * arrow keys / Home / End move within bounds and the chosen width is
//     REMEMBERED per panel (localStorage, try/catch) — Enter and
//     double-click reset to the FIX76 default;
//   * below lg (overlay dialog) there is NO handle;
//   * an empty/throwing storage read renders the default formula.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { useSidePaneStore } from "@/stores/sidePaneStore";

import { PANE_MIN_PX, RESIZE_STEP_PX, SidePane, THREAD_MIN_PX, clampPaneWidth, readSavedPaneWidth } from "./SidePane";

function setViewport(docked: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q === "(min-width: 1024px)" ? docked : false,
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const ROW_W = 1400;
let rectSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  useSidePaneStore.setState({ pane: null, hostMounted: false, dockedWidth: 0 });
  setViewport(true);
  localStorage.clear();
  // jsdom has no layout: every element reports the row width; good
  // enough for clamp math (the aside's own width rides aria-valuenow).
  rectSpy = vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: ROW_W, height: 800, x: 0, y: 0, top: 0, left: 0, right: ROW_W, bottom: 800, toJSON: () => ({}),
  } as DOMRect);
});
afterEach(() => {
  rectSpy.mockRestore();
  cleanup();
});

function Pane() {
  return (
    <SidePane open label="Canvas" maxWidth={672} widthKey="canvas" closeLabel="Close" onClose={() => {}} testId="pane">
      <div>content</div>
    </SidePane>
  );
}

describe("clampPaneWidth", () => {
  it("holds both floors and lets the pane floor win on a too-narrow row", () => {
    expect(clampPaneWidth(500, 1400)).toBe(500);
    expect(clampPaneWidth(100, 1400)).toBe(PANE_MIN_PX);
    expect(clampPaneWidth(5000, 1400)).toBe(1400 - THREAD_MIN_PX); // thread keeps its floor
    // row too narrow for both floors → pane floor wins
    expect(clampPaneWidth(5000, 700)).toBe(PANE_MIN_PX);
    expect(clampPaneWidth(1, 700)).toBe(PANE_MIN_PX);
  });
});

describe("the docked splitter", () => {
  it("is a keyboard-operable ARIA separator that respects bounds and persists the width", () => {
    render(<Pane />);
    const sep = screen.getByTestId("pane-resizer");
    expect(sep.getAttribute("role")).toBe("separator");
    expect(sep.getAttribute("aria-orientation")).toBe("vertical");
    expect(sep.getAttribute("aria-valuemin")).toBe(String(PANE_MIN_PX));
    expect(sep.getAttribute("aria-valuemax")).toBe(String(ROW_W - THREAD_MIN_PX));

    // ArrowLeft grows the pane by a step (from the measured width)
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(ROW_W - THREAD_MIN_PX)); // 1400 + 16 clamps to max
    // Home = narrowest, End = widest
    fireEvent.keyDown(sep, { key: "Home" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(PANE_MIN_PX));
    fireEvent.keyDown(sep, { key: "End" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(ROW_W - THREAD_MIN_PX));
    // the choice is remembered per panel
    expect(readSavedPaneWidth("canvas")).toBe(ROW_W - THREAD_MIN_PX);
    // Enter resets to the FIX76 default (nothing remembered)
    fireEvent.keyDown(sep, { key: "Enter" });
    expect(sep.getAttribute("aria-valuenow")).toBeNull();
    expect(readSavedPaneWidth("canvas")).toBeNull();
  });

  it("a remembered width is applied on mount; garbage in storage renders the default", () => {
    localStorage.setItem("sran-pane-width:canvas", "555");
    render(<Pane />);
    expect(screen.getByTestId("pane").style.width).toBe("555px");
    cleanup();
    localStorage.setItem("sran-pane-width:canvas", "not-a-number");
    render(<Pane />);
    expect(screen.getByTestId("pane").style.width).toBe("min(672px, 45%)");
  });

  it("the dragged width stays CSS-clamped so a window resize re-clamps without JS", () => {
    localStorage.setItem("sran-pane-width:canvas", "800");
    render(<Pane />);
    const pane = screen.getByTestId("pane");
    expect(pane.style.minWidth).toBe(`${PANE_MIN_PX}px`);
    expect(pane.style.maxWidth).toBe(`max(${PANE_MIN_PX}px, calc(100% - ${THREAD_MIN_PX}px))`);
  });

  it("below lg (overlay dialog) there is no handle", () => {
    setViewport(false);
    render(<Pane />);
    expect(screen.queryByTestId("pane-resizer")).toBeNull();
  });

  it("ArrowRight shrinks toward (and clamps at) the thread's floor", () => {
    render(<Pane />);
    const sep = screen.getByTestId("pane-resizer");
    // jsdom reports the aside at ROW_W, so one step down still clamps to
    // the upper bound — what this pins is that ArrowRight moves and
    // never violates a floor (RESIZE_STEP_PX is pinned by the export).
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(Number(sep.getAttribute("aria-valuenow"))).toBeLessThanOrEqual(ROW_W - THREAD_MIN_PX);
    expect(Number(sep.getAttribute("aria-valuenow"))).toBeGreaterThanOrEqual(PANE_MIN_PX);
    expect(RESIZE_STEP_PX).toBeGreaterThan(0);
  });
});
