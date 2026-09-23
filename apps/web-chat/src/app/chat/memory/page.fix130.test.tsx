// FIX130 A4a/A4b — one click on a suggestion does what it says, and every
// change made on this page reaches the desktop host too.
//   · Accept → the suggestion leaves the suggestions list and is under the
//     saved memories at once (no reload), keeping where it came from.
//   · Reject → it is gone (the server turns the DELETE into "rejected", so
//     the extractor never suggests it again).
//   · Every successful mutation calls notifyHostMemoriesChanged(): the
//     desktop answers from a cached copy of the saved memories, and an
//     edit or delete here must not keep reaching its next answer.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockNotify, mockRefresh } = vi.hoisted(() => ({
  mockApi: {
    listMemories: vi.fn(),
    confirmMemory: vi.fn(),
    deleteMemory: vi.fn(),
    pinMemory: vi.fn(),
    clearAllMemories: vi.fn(),
    purgeMemories: vi.fn(),
    createMemory: vi.fn(),
    updateMemory: vi.fn(),
  },
  mockNotify: vi.fn(),
  mockRefresh: vi.fn(async () => {}),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/lib/hostApi", () => ({ notifyHostMemoriesChanged: mockNotify, hostApi: {} }));
vi.mock("@/components/chat/LocalNotesSection", () => ({ LocalNotesSection: () => null }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({
  useMemoryPendingStore: (sel: (s: { refresh: () => Promise<void> }) => unknown) => sel({ refresh: mockRefresh }),
}));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Memory } from "@/lib/types";

import MemorySettingsPage from "./page";

const th = dictionaries.th;
const mem = (over: Partial<Memory>): Memory =>
  ({
    id: "m1",
    scope_type: "global_user",
    memory_type: "explicit_fact",
    content: "x",
    source_type: "assistant_inferred",
    status: "active",
    confidence_score: 0.9,
    importance_score: 0.5,
    is_user_confirmed: true,
    is_pinned: false,
    sensitivity_level: "normal",
    use_count: 0,
    created_at: "2026-09-23T05:00:00Z",
    updated_at: "2026-09-23T05:00:00Z",
    ...over,
  }) as Memory;

const saved = mem({ id: "s1", content: "ชอบคำตอบสั้น", source_type: "user_explicit", source_conversation_id: null });
const suggestion = mem({
  id: "p1",
  content: "อยู่ที่เชียงใหม่",
  status: "pending_confirmation",
  is_user_confirmed: false,
  source_conversation_id: "c-7",
  source_conversation_title: "ร้านกาแฟ",
  evidence_excerpt: "อยู่ที่เชียงใหม่",
});

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  vi.clearAllMocks();
  mockApi.listMemories.mockImplementation(async (p?: { status?: string }) =>
    p?.status === "pending_confirmation" ? { memories: [suggestion], total: 1 } : { memories: [saved], total: 1 },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderPage() {
  render(<MemorySettingsPage />);
  await screen.findByText("อยู่ที่เชียงใหม่", { selector: "p" });
}

describe("FIX130 Memory page — accept / reject / host refresh", () => {
  it("accept moves the suggestion under saved memories at once and keeps its source chat", async () => {
    // The confirm endpoint returns the bare row — no resolved title.
    mockApi.confirmMemory.mockResolvedValue({ ...suggestion, status: "active", is_user_confirmed: true, source_conversation_title: undefined });
    await renderPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: th["memory.pending.confirm"] }));
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: th["memory.pending.confirm"] })).toBeNull());
    const savedList = screen.getByTestId("saved-memories");
    expect(within(savedList).getByText("อยู่ที่เชียงใหม่")).toBeTruthy();
    expect(within(savedList).getByRole("link", { name: "ร้านกาแฟ" }).getAttribute("href")).toBe("/chat/c-7");
    expect(mockApi.confirmMemory).toHaveBeenCalledWith("p1", true);
    expect(mockNotify).toHaveBeenCalledTimes(1);
  });

  it("reject removes the suggestion and tells the host", async () => {
    mockApi.deleteMemory.mockResolvedValue(undefined);
    await renderPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: th["memory.pending.reject"] }));
    });
    await waitFor(() => expect(screen.queryByText("อยู่ที่เชียงใหม่", { selector: "p" })).toBeNull());
    expect(mockApi.deleteMemory).toHaveBeenCalledWith("p1");
    expect(mockNotify).toHaveBeenCalledTimes(1);
  });

  it("delete and pin of a saved memory tell the host too", async () => {
    mockApi.deleteMemory.mockResolvedValue(undefined);
    mockApi.pinMemory.mockResolvedValue({ ...saved, is_pinned: true });
    await renderPage();
    const savedList = screen.getByTestId("saved-memories");
    await act(async () => {
      fireEvent.click(within(savedList).getByRole("button", { name: th["memory.card.pin"] }));
    });
    await waitFor(() => expect(mockNotify).toHaveBeenCalledTimes(1));
    await act(async () => {
      fireEvent.click(within(savedList).getByRole("button", { name: th["memory.card.delete"] }));
    });
    const dialog = await screen.findByRole("dialog");
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: th["memory.confirm.deleteAction"] }));
    });
    await waitFor(() => expect(mockNotify).toHaveBeenCalledTimes(2));
    expect(mockApi.deleteMemory).toHaveBeenCalledWith("s1");
  });

  it("a failed mutation does not claim a change", async () => {
    mockApi.deleteMemory.mockRejectedValue(new Error("boom"));
    await renderPage();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: th["memory.pending.reject"] }));
    });
    await waitFor(() => expect(mockApi.deleteMemory).toHaveBeenCalled());
    expect(mockNotify).not.toHaveBeenCalled();
  });
});
