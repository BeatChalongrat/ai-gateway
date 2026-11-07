// FIX25 §2 — the granted-folders chip. Pins the contract: reads `folders.list`
// on mount (so a cold start shows the real grants — sweep defect #7 was a
// screen that never called it), re-reads on window focus (a revoke made in
// Settings is picked up), adds through `folders.pickAndGrant` (the page names
// no path), zero grants reads as an invitation, and a plain browser renders
// nothing. No op outside the pinned control plane is used.

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ HOSTED_MODE: true }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hostedNow }));

let hostedNow = true;

import { bridge } from "@/lib/bridge";
import { useI18nStore } from "@/lib/i18n";
import { FolderChip, folderBasename } from "./FolderChip";

interface Query { requestId: string; op: string; args: Record<string, unknown> }

function installFakeHost(answer: (op: string, args: Record<string, unknown>) => { ok: boolean; data?: unknown; error?: string }) {
  let handler: ((e: { data: unknown }) => void) | null = null;
  const queries: Query[] = [];
  const host = {
    postMessage: (m: unknown) => {
      const msg = m as { type: string; payload: { requestId: string; op: string; args: Record<string, unknown> } };
      if (msg.type !== "host.query") return;
      queries.push(msg.payload);
      const r = answer(msg.payload.op, msg.payload.args);
      setTimeout(() => handler?.({ data: { type: "host.result", payload: { requestId: msg.payload.requestId, ...r } } }), 0);
    },
    addEventListener: (t: string, cb: (e: { data: unknown }) => void) => {
      if (t === "message") handler = cb;
    },
    nonce: "n1",
  };
  (window as unknown as { sranHost?: unknown }).sranHost = host;
  return { queries };
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

describe("folderBasename", () => {
  it("takes the last segment of a Windows or POSIX path, tolerating a trailing separator", () => {
    expect(folderBasename("C:\\Users\\me\\Desktop\\sran-test-folder")).toBe("sran-test-folder");
    expect(folderBasename("C:\\Users\\me\\Desktop\\sran-test-folder\\")).toBe("sran-test-folder");
    expect(folderBasename("/home/me/work")).toBe("work");
    expect(folderBasename("D:\\")).toBe("D:");
  });
});

describe("FIX25 §2 — FolderChip", () => {
  it("cold start: reads folders.list on mount and shows the grants (not an empty list)", async () => {
    const { queries } = installFakeHost((op) => (op === "folders.list" ? { ok: true, data: { folders: ["C:\\Users\\me\\Desktop\\sran-test-folder", "D:\\data"] } } : { ok: false, error: "unsupported" }));
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("2"));
    expect(screen.getByTestId("folder-chip-trigger").textContent).toContain("2 folders");
    expect(queries.map((q) => q.op)).toEqual(["folders.list"]);
    fireEvent.click(screen.getByTestId("folder-chip-trigger"));
    const list = screen.getByTestId("folder-chip-list");
    expect(list.textContent).toContain("sran-test-folder");
    expect(list.textContent).toContain("D:\\data");
  });

  it("one grant shows its name on the pill", async () => {
    installFakeHost((op) => (op === "folders.list" ? { ok: true, data: { folders: ["C:\\Users\\me\\Desktop\\sran-test-folder"] } } : { ok: false }));
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").textContent).toContain("sran-test-folder"));
  });

  it("zero grants reads as an invitation to add one, not an error", async () => {
    installFakeHost((op) => (op === "folders.list" ? { ok: true, data: { folders: [] } } : { ok: false }));
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("0"));
    expect(screen.getByTestId("folder-chip-trigger").textContent).toContain("Add a folder");
    fireEvent.click(screen.getByTestId("folder-chip-trigger"));
    expect(screen.getByTestId("folder-chip-empty").textContent).toContain("No folders granted yet");
    expect(screen.getByTestId("folder-chip-add")).not.toBeNull();
  });

  it("Add folder calls folders.pickAndGrant (no path from the page) and shows the host's new list", async () => {
    let folders = ["D:\\data"];
    const { queries } = installFakeHost((op) => {
      if (op === "folders.list") return { ok: true, data: { folders } };
      if (op === "folders.pickAndGrant") {
        folders = [...folders, "C:\\Users\\me\\Desktop\\sran-test-folder"];
        return { ok: true, data: { granted: true, folder: folders[1], folders } };
      }
      return { ok: false, error: "unsupported" };
    });
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("1"));
    fireEvent.click(screen.getByTestId("folder-chip-trigger"));
    fireEvent.click(screen.getByTestId("folder-chip-add"));
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("2"));
    const pick = queries.find((q) => q.op === "folders.pickAndGrant")!;
    expect(pick.args).toEqual({}); // the page names no path
    expect(new Set(queries.map((q) => q.op))).toEqual(new Set(["folders.list", "folders.pickAndGrant"]));
  });

  it("a cancelled picker changes nothing", async () => {
    installFakeHost((op) => (op === "folders.list" ? { ok: true, data: { folders: ["D:\\data"] } } : { ok: false, error: "cancelled" }));
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("1"));
    fireEvent.click(screen.getByTestId("folder-chip-trigger"));
    fireEvent.click(screen.getByTestId("folder-chip-add"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5));
    });
    expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("1");
  });

  it("re-reads folders.list when the window regains focus (a revoke made in Settings shows up)", async () => {
    let folders = ["D:\\data", "E:\\more"];
    const { queries } = installFakeHost((op) => (op === "folders.list" ? { ok: true, data: { folders } } : { ok: false }));
    bridge.start();
    render(<FolderChip />);
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("2"));
    folders = ["D:\\data"]; // revoked elsewhere
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(screen.getByTestId("folder-chip-trigger").getAttribute("data-count")).toBe("1"));
    expect(queries.filter((q) => q.op === "folders.list").length).toBeGreaterThanOrEqual(2);
  });

  it("renders nothing in a plain browser", () => {
    hostedNow = false;
    render(<FolderChip />);
    expect(screen.queryByTestId("folder-chip")).toBeNull();
  });
});
