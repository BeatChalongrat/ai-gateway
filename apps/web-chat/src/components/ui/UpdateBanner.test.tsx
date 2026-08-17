// UpdateBanner pins: shows on build mismatch, refresh triggers a reload,
// dismiss hides it for the rest of the page load (no re-show on later
// visibility checks — the hook is latched and dismissed is sticky).

import { act, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateBanner } from "./UpdateBanner";

beforeEach(() => {
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
});

function fireVisible() {
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

async function renderWithUpdate() {
  render(<UpdateBanner />);
  fireVisible();
  await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
}

describe("UpdateBanner", () => {
  it("appears when the deployed build differs from the baked one", async () => {
    await renderWithUpdate();
    expect(screen.getByText("A new version is available")).toBeTruthy();
  });

  it("refresh button reloads the page", async () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    await renderWithUpdate();
    screen.getByText("Refresh").click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("dismiss hides the banner and it stays hidden", async () => {
    await renderWithUpdate();
    act(() => {
      screen.getByLabelText("Dismiss update notice").click();
    });
    expect(screen.queryByRole("status")).toBeNull();
    // Later visibility checks must not resurrect it within this page
    // load — the hook is latched and dismissal is sticky.
    fireVisible();
    await act(() => Promise.resolve());
    expect(screen.queryByRole("status")).toBeNull();
  });
});
