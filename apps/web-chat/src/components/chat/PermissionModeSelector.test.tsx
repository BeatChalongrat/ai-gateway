// FIX25 §1 — the composer's permission-mode selector. Driven through the real
// bridge client against a fake host that answers host.query, so the test pins
// the CONTRACT: reads `settings.get`, writes `settings.set {key:"approvalMode"}`
// (an existing page-only op — no new op), three modes with the tasks-page
// strings, `skip` visibly warned, a refusal reverted, and nothing at all in a
// plain browser.

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hostedNow }));

let hostedNow = true;

import { bridge } from "@/lib/bridge";
import { useI18nStore } from "@/lib/i18n";
import { PermissionModeSelector } from "./PermissionModeSelector";

interface Query { requestId: string; op: string; args: Record<string, unknown> }

/** A fake host: every host.query is answered by `answer(op, args)`. */
function installFakeHost(answer: (op: string, args: Record<string, unknown>) => { ok: boolean; data?: unknown; error?: string }) {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const queries: Query[] = [];
  const emit = (data: unknown) => handler?.({ data });
  const host = {
    postMessage: (m: unknown) => {
      const msg = m as { type: string; payload: { requestId: string; op: string; args: Record<string, unknown> } };
      if (msg.type !== "host.query") return;
      queries.push(msg.payload);
      const r = answer(msg.payload.op, msg.payload.args);
      // Reply asynchronously, like the real pipe.
      setTimeout(() => handler?.({ data: { type: "host.result", payload: { requestId: msg.payload.requestId, ...r } } }), 0);
    },
    addEventListener: (t: string, cb: (e: { data: unknown }) => void) => {
      if (t === "message") handler = cb;
    },
    nonce: "n1",
  };
  (window as unknown as { sranHost?: unknown }).sranHost = host;
  return { queries, emit };
}

beforeEach(() => {
  hostedNow = true;
  useI18nStore.setState({ locale: "en" });
  bridge.resetForTest();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { sranHost?: unknown }).sranHost;
});

describe("FIX25 §1 — PermissionModeSelector", () => {
  it("reads the stored mode through settings.get and shows the tasks-page label", async () => {
    const { queries } = installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: "auto", grantedFolders: [] } } : { ok: false, error: "unsupported" }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("auto"));
    expect(screen.getByTestId("permission-mode-trigger").textContent).toContain("Auto-approves safe");
    expect(queries.map((q) => q.op)).toEqual(["settings.get"]);
  });

  it("offers exactly manual | auto | skip, and skip carries the warning", async () => {
    installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: "manual" } } : { ok: true }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("manual"));
    fireEvent.click(screen.getByTestId("permission-mode-trigger"));
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => i.getAttribute("data-testid"))).toEqual(["permission-mode-manual", "permission-mode-auto", "permission-mode-skip"]);
    expect(items[0]!.getAttribute("aria-checked")).toBe("true");
    expect(items[0]!.textContent).toContain("Asks each action");
    expect(items[1]!.textContent).toContain("Auto-approves safe");
    expect(items[2]!.textContent).toContain("Never asks");
    // §1c.2 / §1c.3: the skip row is visibly different and says it does not stick.
    expect(items[2]!.textContent).toContain("Resets to");
    expect(items[2]!.querySelector("svg.lucide-triangle-alert, svg.lucide-alert-triangle")).not.toBeNull();
    expect(items[0]!.querySelector("svg.lucide-triangle-alert, svg.lucide-alert-triangle")).toBeNull();
  });

  it("picking a mode writes settings.set {key: approvalMode} — the existing op — and updates the pill", async () => {
    const { queries } = installFakeHost((op, args) => (op === "settings.get" ? { ok: true, data: { approvalMode: "manual" } } : { ok: true, data: { key: args["key"], restartRequired: false } }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("manual"));
    fireEvent.click(screen.getByTestId("permission-mode-trigger"));
    fireEvent.click(screen.getByTestId("permission-mode-skip"));
    await waitFor(() => expect(queries.some((q) => q.op === "settings.set")).toBe(true));
    const write = queries.find((q) => q.op === "settings.set")!;
    expect(write.args).toEqual({ key: "approvalMode", value: "skip" });
    expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("skip");
    expect(screen.getByTestId("permission-mode-trigger").textContent).toContain("Never asks");
    // The op set is the pinned control plane: nothing but settings.get / settings.set was used.
    expect(new Set(queries.map((q) => q.op))).toEqual(new Set(["settings.get", "settings.set"]));
  });

  it("a host refusal reverts the pill to the stored mode", async () => {
    installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: "auto" } } : { ok: false, error: "refused" }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("auto"));
    fireEvent.click(screen.getByTestId("permission-mode-trigger"));
    fireEvent.click(screen.getByTestId("permission-mode-skip"));
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("auto"));
  });

  it("the host's startup reset notice moves the pill to the reset mode (the read can race the reset)", async () => {
    // Seen live: the page read `skip` in the gap between the sidecar answering
    // and the host writing `manual` back. The notice is the pill's cue too.
    const { emit } = installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: "skip" } } : { ok: true }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("skip"));
    act(() => emit({ type: "host.notice", payload: { kind: "approval-mode-reset", from: "skip", to: "manual" } }));
    expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("manual");
    expect(screen.getByTestId("permission-mode-trigger").getAttribute("aria-label")).toBe("Permissions: Asks each action");
  });

  it("re-reads the mode when the window regains focus", async () => {
    let stored = "auto";
    installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: stored } } : { ok: true }));
    bridge.start();
    render(<PermissionModeSelector />);
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("auto"));
    stored = "manual";
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("manual"));
  });

  it("renders nothing in a plain browser", () => {
    hostedNow = false;
    render(<PermissionModeSelector />);
    expect(screen.queryByTestId("permission-mode-selector")).toBeNull();
  });

  it("an unknown stored value shows the neutral label, not a made-up mode", async () => {
    installFakeHost((op) => (op === "settings.get" ? { ok: true, data: { approvalMode: "yolo" } } : { ok: true }));
    bridge.start();
    render(<PermissionModeSelector />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    expect(screen.getByTestId("permission-mode-trigger").getAttribute("data-mode")).toBe("");
    expect(screen.getByTestId("permission-mode-trigger").textContent).toContain("Permissions");
  });
});
