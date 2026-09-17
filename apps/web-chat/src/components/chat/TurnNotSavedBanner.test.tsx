// FIX121 C4 — the "this turn was not saved to the server" strip: appears only
// when the desktop host says so, names the run, is dismissible, and a
// malformed notice or a plain browser renders nothing.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hostedNow }));

let hostedNow = true;

import { bridge } from "@/lib/bridge";
import { TurnNotSavedBanner } from "./TurnNotSavedBanner";

function installFakeHost() {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const fake = {
    postMessage: vi.fn(),
    addEventListener: (t: string, cb: (e: { data: unknown }) => void) => {
      if (t === "message") handler = cb;
    },
    emit: (data: unknown) => handler?.({ data }),
  };
  (window as unknown as { sranHost?: unknown }).sranHost = { ...fake, nonce: "n1" };
  return fake;
}
const notice = (payload: Record<string, unknown>) => ({ type: "host.notice", payload });

beforeEach(() => {
  hostedNow = true;
  bridge.resetForTest();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { sranHost?: unknown }).sranHost;
});

describe("FIX121 — TurnNotSavedBanner", () => {
  it("says the turn was not saved, names the run, and can be dismissed", () => {
    const host = installFakeHost();
    bridge.start();
    render(<TurnNotSavedBanner />);
    expect(screen.queryByTestId("turn-not-saved-banner")).toBeNull();

    act(() => host.emit(notice({ kind: "turn-not-saved", conversationId: "c1", runId: "8f1d2c3b-run", reason: "failed", status: 503 })));
    const banner = screen.getByTestId("turn-not-saved-banner");
    expect(banner.getAttribute("role")).toBe("alert");
    expect(banner.textContent).toContain("could not be saved to the server");
    expect(banner.textContent).toContain("8f1d2c3b-run");

    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByTestId("turn-not-saved-banner")).toBeNull();
  });

  it("a partial save has its own sentence", () => {
    const host = installFakeHost();
    bridge.start();
    render(<TurnNotSavedBanner />);
    act(() => host.emit(notice({ kind: "turn-not-saved", conversationId: "c1", reason: "rejected", detail: "a_1: status must be…" })));
    expect(screen.getByTestId("turn-not-saved-banner").textContent).toContain("Part of this turn");
  });

  it("a malformed notice renders nothing", () => {
    const host = installFakeHost();
    bridge.start();
    render(<TurnNotSavedBanner />);
    act(() => host.emit(notice({ kind: "turn-not-saved", reason: "failed" }))); // no conversation
    act(() => host.emit(notice({ kind: "turn-not-saved", conversationId: "c1", reason: "exploded" })));
    expect(screen.queryByTestId("turn-not-saved-banner")).toBeNull();
  });

  it("a plain browser renders nothing", () => {
    hostedNow = false;
    render(<TurnNotSavedBanner />);
    expect(screen.queryByTestId("turn-not-saved-banner")).toBeNull();
  });
});
