// FIX22 §2 — the update banner: renders ONLY when the host posts a
// `host.notice` with a newer version, is dismissible, stays dismissed for
// that version across a remount (localStorage), and shows again for the next
// version. Carries no link / download. In a plain browser (no host) it
// renders nothing at all.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hostedNow }));

let hostedNow = true;

import { bridge } from "@/lib/bridge";
import { UpdateBanner, UPDATE_DISMISSED_KEY } from "./UpdateBanner";

interface FakeHost {
  postMessage: ReturnType<typeof vi.fn>;
  addEventListener: (t: string, cb: (e: { data: unknown }) => void) => void;
  emit: (data: unknown) => void;
}

function installFakeHost(): FakeHost {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const fake: FakeHost = {
    postMessage: vi.fn(),
    addEventListener: (t, cb) => {
      if (t === "message") handler = cb;
    },
    emit: (data) => handler?.({ data }),
  };
  (window as unknown as { sranHost?: unknown }).sranHost = { ...fake, nonce: "n1" };
  return fake;
}

beforeEach(() => {
  hostedNow = true;
  window.localStorage.clear();
  bridge.resetForTest();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { sranHost?: unknown }).sranHost;
});

describe("FIX22 §2 — UpdateBanner", () => {
  it("renders nothing until a newer-version notice arrives, then shows it", () => {
    const host = installFakeHost();
    bridge.start();
    render(<UpdateBanner />);
    expect(screen.queryByTestId("update-banner")).toBeNull();

    act(() => host.emit({ type: "host.notice", payload: { kind: "update-available", latest: "0.1.0-e5", current: "0.1.0-e4" } }));
    const banner = screen.getByTestId("update-banner");
    expect(banner.textContent).toContain("0.1.0-e5");
    expect(banner.textContent).toContain("0.1.0-e4");
    expect(banner.querySelector("a")).toBeNull(); // no link, no download
  });

  it("dismiss hides it and it STAYS dismissed for that version after a remount", () => {
    const host = installFakeHost();
    bridge.start();
    const { unmount } = render(<UpdateBanner />);
    act(() => host.emit({ type: "host.notice", payload: { kind: "update-available", latest: "0.1.0-e5", current: "0.1.0-e4" } }));
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByTestId("update-banner")).toBeNull();
    expect(window.localStorage.getItem(UPDATE_DISMISSED_KEY)).toBe("0.1.0-e5");

    unmount();
    render(<UpdateBanner />);
    act(() => host.emit({ type: "host.notice", payload: { kind: "update-available", latest: "0.1.0-e5", current: "0.1.0-e4" } }));
    expect(screen.queryByTestId("update-banner")).toBeNull();

    // The NEXT version shows again.
    act(() => host.emit({ type: "host.notice", payload: { kind: "update-available", latest: "0.1.0-e6", current: "0.1.0-e4" } }));
    expect(screen.getByTestId("update-banner").textContent).toContain("0.1.0-e6");
  });

  it("a malformed notice (no version) renders nothing", () => {
    const host = installFakeHost();
    bridge.start();
    render(<UpdateBanner />);
    act(() => host.emit({ type: "host.notice", payload: { kind: "update-available", latest: "", current: "0.1.0-e4" } }));
    act(() => host.emit({ type: "host.notice", payload: { kind: "something-else", latest: "9.9.9", current: "0.1.0-e4" } }));
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });

  it("plain browser (not hosted) → nothing, ever", () => {
    hostedNow = false;
    render(<UpdateBanner />);
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });
});
