// AM6 — the knowledge chip outside a conversation. The chat binds a knowledge
// base to a conversation, so the chip shows nothing where there is none (the
// landing page). An agent's workspace has no conversation and keeps the
// choice itself: it asks for the chip as it is (`standalone`).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { listKnowledgeBases: vi.fn(), getKnowledgeAvailability: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));

import { useI18nStore } from "@/lib/i18n";
import type { KnowledgeBase } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

import { KBSelector } from "./KBSelector";

const chatInitial = useChatStore.getState();
const kbInitial = useKnowledgeStore.getState();
const kb = (id: string, name: string): KnowledgeBase => ({
  id, name, description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50, is_active: true,
  document_count: 3, total_chunks: 30, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
});

beforeEach(() => {
  vi.clearAllMocks();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useChatStore.setState({ ...chatInitial }, true);
  useI18nStore.setState({ locale: "th" });
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb("kb1", "ผังบัญชี"), kb("kb2", "นโยบายค่าใช้จ่าย")] });
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("AM6 — the knowledge chip without a conversation", () => {
  it("the chat's rule stays: no conversation, no chip", () => {
    const { container } = render(<KBSelector conversationId={null} activeKBId="kb1" onPick={vi.fn(async () => {})} />);
    expect(container.innerHTML).toBe("");
  });

  it("asked for as it is, it is the same chip a conversation shows", async () => {
    const chat = render(<KBSelector conversationId="c1" activeKBId="kb1" onPick={vi.fn(async () => {})} />);
    await flush();
    const want = chat.container.innerHTML;
    chat.unmount();

    const { container } = render(<KBSelector conversationId={null} standalone activeKBId="kb1" onPick={vi.fn(async () => {})} />);
    await flush();
    expect(container.innerHTML).toBe(want);
    expect(screen.getByText("ผังบัญชี")).toBeInTheDocument();
  });

  it("picks and removes through the caller", async () => {
    const onPick = vi.fn(async () => {});
    render(<KBSelector conversationId={null} standalone activeKBId="kb1" onPick={onPick} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "ฐานความรู้: ผังบัญชี" }));
    fireEvent.click(screen.getByText("นโยบายค่าใช้จ่าย"));
    expect(onPick).toHaveBeenLastCalledWith("kb2");
    fireEvent.click(screen.getByRole("button", { name: "เอาฐานความรู้ออกจากแชทนี้" }));
    expect(onPick).toHaveBeenLastCalledWith(null);
  });
});
