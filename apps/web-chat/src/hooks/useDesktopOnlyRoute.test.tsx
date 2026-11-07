// FIX34.1 Part F — desktop-only routes never render a wall on the web.

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: false }));

// HOSTED_MODE is a module constant; a getter lets each test flip the surface.
vi.mock("@/lib/auth", () => ({
  get HOSTED_MODE() {
    return state.hosted;
  },
}));

import { desktopOnlyRedirect, useDesktopOnlyRoute } from "./useDesktopOnlyRoute";

function Page({ fallback }: { fallback?: string }) {
  const hosted = useDesktopOnlyRoute(fallback);
  return hosted ? <p>desktop screen</p> : <p>nothing</p>;
}

const redirect = vi.spyOn(desktopOnlyRedirect, "to").mockImplementation(() => {});

afterEach(cleanup);
beforeEach(() => {
  redirect.mockClear();
});

describe("useDesktopOnlyRoute", () => {
  it("web: replaces the route with the fallback and renders no desktop UI", () => {
    state.hosted = false;
    const { container } = render(<Page />);
    expect(redirect).toHaveBeenCalledWith("/chat");
    expect(container.textContent).not.toContain("desktop screen");
    expect(container.textContent).not.toMatch(/Desktop app/i);
  });

  it("web: honours a custom fallback", () => {
    state.hosted = false;
    render(<Page fallback="/chat/memory" />);
    expect(redirect).toHaveBeenCalledWith("/chat/memory");
  });

  it("desktop: never redirects and shows the screen", () => {
    state.hosted = true;
    const { container } = render(<Page />);
    expect(redirect).not.toHaveBeenCalled();
    expect(container.textContent).toContain("desktop screen");
  });
});
