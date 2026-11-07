// FIX132 B4 — the composer says which knowledge base this chat uses.
// Before: the pill only knew a KB's name once its dropdown had been opened
// (the list loaded lazily), so a chat bound to "คู่มือ HR" showed the generic
// "ฐานความรู้" pill as if nothing were attached; taking it off took two
// clicks inside the dropdown; and a chat inside a project whose knowledge
// base the server uses showed nothing at all.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { listKnowledgeBases: vi.fn(), getKnowledgeAvailability: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation, KnowledgeBase, Project } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

import { KBSelector } from "./KBSelector";

const th = dictionaries.th as Record<string, string>;
const chatInitial = useChatStore.getState();
const kbInitial = useKnowledgeStore.getState();
const kb = (id: string, name: string): KnowledgeBase => ({
  id, name, description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50, is_active: true,
  document_count: 3, total_chunks: 30, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
});
const conv = (over: Partial<Conversation>): Conversation =>
  ({ id: "c1", title: "T", model_id: "m", message_count: 0, created_at: "", updated_at: "", ...over }) as Conversation;

beforeEach(() => {
  vi.clearAllMocks();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useChatStore.setState({ ...chatInitial, conversations: [conv({})], projects: [] }, true);
  useI18nStore.setState({ locale: "th" });
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb("kb1", "คู่มือ HR"), kb("kb2", "สัญญา")] });
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("FIX132 — the attached knowledge base is named in the composer", () => {
  it("shows the KB's name without opening the dropdown", async () => {
    render(<KBSelector conversationId="c1" activeKBId="kb1" onPick={vi.fn(async () => {})} />);
    await flush();
    expect(mockApi.listKnowledgeBases).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(th["kb.picker.active"].replace("{name}", "คู่มือ HR"))).toBeInTheDocument();
    expect(screen.getByText("คู่มือ HR")).toBeInTheDocument();
  });

  it("the chip's × takes it off this chat in one click", async () => {
    const onPick = vi.fn(async () => {});
    render(<KBSelector conversationId="c1" activeKBId="kb1" onPick={onPick} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: th["kb.chip.remove"] }));
    expect(onPick).toHaveBeenCalledWith(null);
  });

  it("nothing attached: the plain picker, no ×, no list fetched until it is opened", async () => {
    render(<KBSelector conversationId="c1" activeKBId={null} onPick={vi.fn(async () => {})} />);
    await flush();
    expect(screen.getByLabelText(th["kb.picker.pick"])).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: th["kb.chip.remove"] })).toBeNull();
    expect(mockApi.listKnowledgeBases).not.toHaveBeenCalled();
  });

  it("a chat in a project with a knowledge base shows the project's KB (not removable here)", async () => {
    const project = { id: "p1", name: "งาน HR", instructions: "", knowledge_base_id: "kb2", conversation_count: 1, created_at: "", updated_at: "" } as Project;
    useChatStore.setState({ conversations: [conv({ project_id: "p1" })], projects: [project] });
    render(<KBSelector conversationId="c1" activeKBId={null} onPick={vi.fn(async () => {})} />);
    await flush();
    expect(screen.getByLabelText(th["kb.chip.fromProject"].replace("{name}", "สัญญา"))).toBeInTheDocument();
    expect(screen.getByText("สัญญา")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: th["kb.chip.remove"] })).toBeNull();
    fireEvent.click(screen.getByLabelText(th["kb.chip.fromProject"].replace("{name}", "สัญญา")));
    expect(screen.getByText(th["kb.picker.projectNote"].replace("{name}", "สัญญา"))).toBeInTheDocument();
  });
});
