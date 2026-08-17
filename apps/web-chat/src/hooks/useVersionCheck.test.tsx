// Version-poll update detection. Pins: mismatch on a visibility check
// flips updateAvailable (once, latched); same build stays false; no
// auto-reload side effects.

import { act, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useVersionCheck } from "./useVersionCheck";

function Probe() {
  const update = useVersionCheck();
  return <div data-testid="probe">{update ? "update" : "current"}</div>;
}

const BAKED = process.env.NEXT_PUBLIC_BUILD_ID;

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_BUILD_ID", "build-1");
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function mockVersion(build: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ build }),
      } as unknown as Response),
    ),
  );
}

function fireVisible() {
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

describe("useVersionCheck", () => {
  it("stays current when the server build matches", async () => {
    mockVersion("build-1");
    render(<Probe />);
    fireVisible();
    // Give the async check a tick.
    await act(() => Promise.resolve());
    expect(screen.getByTestId("probe").textContent).toBe("current");
  });

  it("flips to update on mismatch (via visibility check) and latches", async () => {
    mockVersion("build-2");
    render(<Probe />);
    fireVisible();
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("update"),
    );
    // Latched: further checks don't fetch again.
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    fireVisible();
    await act(() => Promise.resolve());
    expect(
      (fetch as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBe(calls);
  });

  it("never reloads on its own", async () => {
    mockVersion("build-2");
    const reload = vi.fn();
    // jsdom's location.reload is not configurable-safe everywhere;
    // assert indirectly — the hook only sets state, and this spy on a
    // stubbed global would catch any call path that tried.
    vi.stubGlobal("location", { ...window.location, reload });
    render(<Probe />);
    fireVisible();
    await waitFor(() =>
      expect(screen.getByTestId("probe").textContent).toBe("update"),
    );
    expect(reload).not.toHaveBeenCalled();
  });
});

// Restore any real baked id for other suites.
if (BAKED) process.env.NEXT_PUBLIC_BUILD_ID = BAKED;
