// FIX130 C2 — no knowledge-base affordance leads to a feature that cannot
// work. NIGHT2 F27 / the FIX130 test stack: every upload failed ("embedding
// failed: gateway unavailable: status=503"), yet the composer had a
// ฐานความรู้ button, the project panel a dropdown promising "chats here can
// search the attached knowledge base", the account menu an entry, and the
// knowledge page offered create + upload. Every one of them now asks the
// server (GET /knowledge-bases/availability) and shows nothing unless it
// says available; the knowledge page says why in Thai.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    getKnowledgeBase: vi.fn(),
    uploadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    listConversations: vi.fn(),
    listProjects: vi.fn(),
    searchConversations: vi.fn(),
    getPreferences: vi.fn(),
    listConversationsPage: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/chat",
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "somchai@acme.ai.gateway" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }) }));

import KnowledgeBasePage from "@/app/chat/knowledge/page";
import ProjectsPage from "@/app/chat/projects/page";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { KnowledgeBase, Project } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import * as knowledgeStoreModule from "@/stores/knowledgeStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";
import { Sidebar } from "./Sidebar";

const chatInitial = useChatStore.getState();
const kbInitial = useKnowledgeStore.getState();
const th = dictionaries.th;
const kb: KnowledgeBase = {
  id: "kb1", name: "คู่มือ", description: "", embedding_model: "text-embedding-3-small", chunk_size: 512, chunk_overlap: 50,
  is_active: true, document_count: 1, total_chunks: 0, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
};

function serverSays(available: boolean) {
  mockApi.getKnowledgeAvailability.mockResolvedValue(available ? { available: true } : { available: false, reason: "embeddings_failed" });
}

beforeEach(() => {
  vi.clearAllMocks();
  // Old trees have no such seam; the test still runs there (and fails on
  // what it asserts, not on a missing export).
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useChatStore.setState({ ...chatInitial, conversationsLoaded: true }, true);
  useModelStore.setState({ models: [] });
  useI18nStore.setState({ locale: "th" });
  localStorage.clear();
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [] });
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
  mockApi.getPreferences.mockResolvedValue({ custom_instructions_about: "", custom_instructions_respond: "" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useI18nStore.setState({ locale: "en" });
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("FIX130 C2 — the knowledge page says why, and offers nothing that fails", () => {
  it("unavailable: a Thai reason, no create, no upload", async () => {
    serverSays(false);
    mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb] });
    mockApi.getKnowledgeBase.mockResolvedValue({ knowledge_base: kb, documents: [] });
    render(<KnowledgeBasePage />);
    const notice = await screen.findByTestId("kb-unavailable");
    expect(notice).toHaveTextContent(th["kb.unavailable.title"]);
    expect(notice).toHaveTextContent(th["kb.unavailable.embeddings_failed"]);
    expect(screen.queryByText(th["kb.create"])).toBeNull();
    expect(screen.queryByText(th["kb.create.full"])).toBeNull();
    // An existing KB stays listed (it can be deleted), but nothing can be uploaded into it.
    fireEvent.click(await screen.findByText("คู่มือ"));
    await flush();
    expect(screen.queryByText(th["kb.docs.upload"])).toBeNull();
  });

  it("available: create is offered and no notice shows", async () => {
    serverSays(true);
    render(<KnowledgeBasePage />);
    expect(await screen.findByText(th["kb.create.full"])).toBeInTheDocument();
    expect(screen.queryByTestId("kb-unavailable")).toBeNull();
  });
});

describe("FIX130 C2 — the entry points are gone while it cannot work", () => {
  function mountComposer() {
    return render(
      <ChatInput conversationId="c1" disabled={false} streaming={false} activeModelId={AUTO_MODEL_ID}
        onSend={vi.fn()} onStop={vi.fn()} activeKBId={null} onKBPick={vi.fn(async () => {})} />,
    );
  }

  it("composer: no ฐานความรู้ button when unavailable; there when available", async () => {
    serverSays(false);
    mountComposer();
    await flush();
    expect(screen.queryByLabelText(th["kb.picker.pick"])).toBeNull();
    cleanup();
    (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
    serverSays(true);
    mountComposer();
    expect(await screen.findByLabelText(th["kb.picker.pick"])).toBeInTheDocument();
  });

  it("account menu: no knowledge-base entry when unavailable", async () => {
    serverSays(false);
    render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const items = [...screen.getByRole("menu").querySelectorAll('[role="menuitem"]')].map((el) => el.textContent?.trim() ?? "");
    expect(items).not.toContain(th["menu.knowledgeBase"]);
    expect(items).toContain(th["menu.projects"]);
  });

  it("project panel: no knowledge-base dropdown or its promise when unavailable", async () => {
    serverSays(false);
    const project: Project = {
      id: "p1", name: "งานไตรมาส", instructions: "", knowledge_base_id: null, conversation_count: 0,
      created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
    } as Project;
    useChatStore.setState({ projects: [project], projectsLoaded: true, projectsError: null });
    render(<ProjectsPage />);
    fireEvent.click(await screen.findByText("งานไตรมาส"));
    await flush();
    expect(screen.queryByText(th["projects.kb.hint"])).toBeNull();
    expect(screen.queryByText(th["projects.kb.label"])).toBeNull();
  });
});
