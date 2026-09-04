// FIX59 Part 2 — the two bottom-centred pills must land in the same place.
//
// `position: fixed` + `left-1/2` centres on the SCREEN. On a chat route
// the sidebar owns the left edge, so the chat column's centre is half a
// sidebar to the right of that. The Toast already compensated; the
// UpdateBanner did not, and sat ~130px to the left of the column it
// belonged to whenever the sidebar was open.
//
// This pins the fix as a RELATIONSHIP rather than as a magic number:
// whatever the offset is, both pills use it. A future change to the
// sidebar width that updates only one of them fails here.

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let collapsed = false;
let isDesktop = true;

vi.mock("@/stores/sidebarStore", () => ({
  useSidebarStore: (sel: (s: { collapsed: boolean }) => unknown) => sel({ collapsed }),
}));

function setViewport(desktop: boolean) {
  isDesktop = desktop;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (q: string) => ({
      matches: isDesktop,
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}

beforeEach(() => {
  collapsed = false;
  setViewport(true);
  vi.stubEnv("NEXT_PUBLIC_BUILD_ID", "build-1");
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ build: "build-2" }),
      } as unknown as Response),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

/** Render the banner alone and return its inline `left`. */
async function bannerLeft(): Promise<string> {
  const { UpdateBanner } = await import("./UpdateBanner");
  render(<UpdateBanner />);
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
  const el = screen.getByRole("status") as HTMLElement;
  return el.style.left;
}

/** Render a toast alone and return its inline `left`. */
async function toastLeft(): Promise<string> {
  const { ToastViewport, toast } = await import("./Toast");
  render(<ToastViewport />);
  act(() => toast("hello"));
  const el = screen.getByRole("status") as HTMLElement;
  return el.style.left;
}

describe("bottom-centred pills line up", () => {
  it("agree with the sidebar expanded on desktop — offset by half the sidebar", async () => {
    const b = await bannerLeft();
    cleanup();
    const t = await toastLeft();
    expect(b).toBe(t);
    // Not merely equal: both must actually be shifted off the screen
    // midline, or "they agree" would also be satisfied by both being
    // wrong in the same way.
    expect(b).toBe("calc(50% + 130px)");
  });

  it("agree with the sidebar collapsed — centred on the screen", async () => {
    collapsed = true;
    const b = await bannerLeft();
    cleanup();
    const t = await toastLeft();
    expect(b).toBe(t);
    expect(b).toBe("calc(50% + 0px)");
  });

  it("agree on mobile, where the sidebar is an overlay", async () => {
    collapsed = false;
    setViewport(false);
    const b = await bannerLeft();
    cleanup();
    const t = await toastLeft();
    expect(b).toBe(t);
    // The sidebar overlays the content instead of displacing it, so the
    // chat column's centre IS the screen's centre.
    expect(b).toBe("calc(50% + 0px)");
  });

  it("the banner no longer relies on the screen-centring class", async () => {
    const { UpdateBanner } = await import("./UpdateBanner");
    render(<UpdateBanner />);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
    const el = screen.getByRole("status") as HTMLElement;
    // `left-1/2` would override the inline style's intent by racing it;
    // the class has to be gone, not just supplemented.
    expect(el.className).not.toContain("left-1/2");
    expect(el.className).toContain("-translate-x-1/2");
  });
});
