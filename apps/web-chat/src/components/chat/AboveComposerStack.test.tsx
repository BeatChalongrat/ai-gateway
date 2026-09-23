// FIX130 C4 — the stack on top of the composer publishes its height while it
// is mounted, and nothing once it is gone; useAboveComposer counts it only
// while a composer is docked (a centred composer publishes 0).
import { cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAboveComposer } from "@/hooks/useBottomInset";
import { useComposerStore } from "@/stores/composerStore";

import { AboveComposerStack } from "./AboveComposerStack";

beforeEach(() => {
  useComposerStore.setState({ height: 0, above: 0, badge: 0 });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const h = this.dataset.testid === "above-composer" ? 64 : 0;
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: h, width: 0, height: h, toJSON: () => ({}) } as DOMRect;
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FIX130 C4 — AboveComposerStack", () => {
  it("publishes its height while mounted and 0 after", () => {
    const view = render(<AboveComposerStack><p>strip</p></AboveComposerStack>);
    expect(useComposerStore.getState().above).toBe(64);
    view.unmount();
    expect(useComposerStore.getState().above).toBe(0);
  });

  it("counts only over a docked composer", () => {
    useComposerStore.setState({ height: 0, above: 64 });
    expect(renderHook(() => useAboveComposer()).result.current).toBe(0);
    useComposerStore.setState({ height: 96, above: 64 });
    expect(renderHook(() => useAboveComposer()).result.current).toBe(64);
  });
});
