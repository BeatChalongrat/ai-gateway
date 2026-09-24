// FIX86 Part A — the bottom of the chat column is a RELATIONSHIP.
//
// FIX59's FloatingPillAlignment test pinned the horizontal axis: whatever
// the sidebar offset is, both pills use it. This pins the vertical one:
// whatever the composer's measured height is, every bottom-floating
// element sits exactly that much higher. A future redesign that moves one
// consumer and not the others — or reintroduces a literal — fails here.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidebarStore", () => ({
  useSidebarStore: (sel: (s: { collapsed: boolean }) => unknown) => sel({ collapsed: true }),
}));
vi.mock("@/hooks/useUsage", () => ({
  useUsage: () => ({
    data: {
      credits: { total: 100, used: 40, remaining: 60, reset_date: null },
      usage_by_model: [],
      total_credits_used: 40,
      period: "lifetime",
    },
    isError: false,
    isStale: false,
    isFetching: false,
    refetch: () => Promise.resolve(),
  }),
}));

import { BADGE_GAP, PILL_GAP } from "./useBottomInset";

// `vi.resetModules()` between tests means every dynamic import below gets
// a fresh module graph — so the store must be imported with the
// consumers, not once at the top, or the test would drive a different
// store instance from the one the components read.
type Store = typeof import("@/stores/composerStore")["useComposerStore"];

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (q: string) => ({ matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  vi.stubEnv("NEXT_PUBLIC_BUILD_ID", "build-1");
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ build: "build-2" }) } as unknown as Response)));
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

const px = (v: string) => Number.parseFloat(v || "0");

async function mountConsumers(): Promise<{ read: () => Record<"badge" | "toast" | "update" | "error", number>; store: Store }> {
  const { useComposerStore } = await import("@/stores/composerStore");
  useComposerStore.setState({ height: 0 });
  const { CreditBadge } = await import("@/components/chat/CreditBadge");
  const { ToastViewport, toast } = await import("@/components/ui/Toast");
  const { UpdateBanner } = await import("@/components/ui/UpdateBanner");
  const { ErrorBanner } = await import("@/components/ui/ErrorBanner");
  render(
    <>
      <CreditBadge />
      <ToastViewport />
      <UpdateBanner />
      <ErrorBanner message="boom" />
    </>,
  );
  act(() => toast("hello"));
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await screen.findAllByRole("status");
  const read = () => ({
    badge: px((screen.getByTestId("credit-badge") as HTMLElement).style.bottom),
    // FIX132 F18 — role="status" is now the toast's always-mounted live
    // region; the visible pill is data-testid="toast".
    toast: px((screen.getByTestId("toast") as HTMLElement).style.bottom),
    update: px((screen.getAllByRole("status").find((e) => /update|refresh|เวอร์ชัน/i.test(e.textContent || "")) as HTMLElement).style.bottom),
    error: px((screen.getByTestId("error-banner") as HTMLElement).style.bottom),
  });
  return { read, store: useComposerStore };
}

describe("everything that floats at the bottom sits above the composer's measured height", () => {
  it("with no composer mounted every consumer is at its base gap (no literal above zero)", async () => {
    const { read } = await mountConsumers();
    const b = read();
    expect(b.badge).toBe(BADGE_GAP);
    expect(b.toast).toBe(PILL_GAP);
    expect(b.update).toBe(PILL_GAP);
    expect(b.error).toBe(PILL_GAP);
  });

  it("when the composer publishes a height, EVERY consumer moves up by exactly that height", async () => {
    const { read, store } = await mountConsumers();
    const before = read();
    act(() => store.getState().setHeight(187));
    const after = read();
    for (const k of ["badge", "toast", "update", "error"] as const) {
      expect(after[k] - before[k], `${k} must move by the composer's height`).toBe(187);
    }
    // and back down when it shrinks — the relationship holds mid-change, not only at first paint
    act(() => store.getState().setHeight(64));
    const shrunk = read();
    for (const k of ["badge", "toast", "update", "error"] as const) {
      expect(shrunk[k] - before[k]).toBe(64);
    }
  });

  it("the composer is the producer: mounting it publishes its height, unmounting publishes 0", async () => {
    const { useComposerHeightProbe } = await import("./useBottomInset");
    const { useComposerStore } = await import("@/stores/composerStore");
    const { useRef } = await import("react");
    function Probe() {
      const ref = useRef<HTMLDivElement>(null);
      useComposerHeightProbe(ref);
      return <div ref={ref} data-testid="composer" />;
    }
    // docked at the bottom: 142 px tall, its bottom edge 24 px above the
    // viewport bottom (the quick window's disclaimer sits there)
    const vh = window.innerHeight;
    const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ height: 142, width: 0, x: 0, y: vh - 166, top: vh - 166, left: 0, right: 0, bottom: vh - 24, toJSON: () => ({}) } as DOMRect);
    const { unmount } = render(<Probe />);
    // not the height: the distance from the viewport bottom to the TOP edge
    expect(useComposerStore.getState().height).toBe(166);
    unmount();
    expect(useComposerStore.getState().height).toBe(0);
    spy.mockRestore();
  });

  it("a composer that is not docked at the bottom (the centred landing composer) publishes 0", async () => {
    const { insetForRect, DOCKED_TOLERANCE } = await import("./useBottomInset");
    expect(insetForRect({ top: 300, bottom: 420 }, 900)).toBe(0);
    expect(insetForRect({ top: 900 - 149, bottom: 900 - 24 }, 900)).toBe(149);
    expect(insetForRect({ top: 700, bottom: 900 }, 900)).toBe(200);
    expect(insetForRect({ top: 700, bottom: 900 - DOCKED_TOLERANCE - 1 }, 900)).toBe(0);
  });

  it("no bottom-floating element carries a vertical literal any more", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const root = path.resolve(__dirname, "..");
    const files = [
      "components/chat/CreditBadge.tsx",
      "components/ui/Toast.tsx",
      "components/ui/UpdateBanner.tsx",
      "components/ui/ErrorBanner.tsx",
      "app/AppProviders.tsx",
    ];
    for (const f of files) {
      // comments may quote the old shape; class strings may not
      const src = fs.readFileSync(path.join(root, f), "utf8").replace(/^\s*\/\/.*$/gm, "");
      expect(src, `${f} still positions itself with a literal`).not.toMatch(/\b(fixed|absolute) bottom-(6|\[\d+px\])/);
    }
  });
});
