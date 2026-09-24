// FIX132 B4 — the knowledge base runs on local models (kb-models), so the
// only reasons it cannot be used are that those models are still loading or
// that they failed. The page says which in Thai, never tells anyone to fix an
// OpenAI key (there is none in the path any more), and — because loading
// ends by itself — asks again by itself, so the knowledge base appears
// without a reload the moment it can work.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    listKBDocuments: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));

import KnowledgeBasePage from "@/app/chat/knowledge/page";
import { useKnowledgeAvailable } from "@/hooks/useKnowledgeAvailable";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import * as knowledgeStoreModule from "@/stores/knowledgeStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

const th = dictionaries.th as Record<string, string>;
const en = dictionaries.en as Record<string, string>;
const kbInitial = useKnowledgeStore.getState();
const recheck = (knowledgeStoreModule as { KB_AVAILABILITY_RECHECK?: { loadingMs: number } }).KB_AVAILABILITY_RECHECK;
const originalLoadingMs = recheck?.loadingMs;

beforeEach(() => {
  vi.clearAllMocks();
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useI18nStore.setState({ locale: "th" });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [] });
  mockApi.listKBDocuments.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  if (recheck && originalLoadingMs !== undefined) recheck.loadingMs = originalLoadingMs;
  useI18nStore.setState({ locale: "en" });
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("FIX132 — why the knowledge base cannot be used, in Thai", () => {
  it("models_loading: the search system is starting — try again shortly", async () => {
    mockApi.getKnowledgeAvailability.mockResolvedValue({ available: false, reason: "models_loading" });
    render(<KnowledgeBasePage />);
    const notice = await screen.findByTestId("kb-unavailable");
    expect(th["kb.unavailable.models_loading"]).toBe("ระบบค้นหาในเอกสารกำลังเริ่มทำงาน ลองใหม่ในอีกสักครู่");
    expect(notice).toHaveTextContent(th["kb.unavailable.models_loading"]);
    expect(notice.textContent).not.toMatch(/openai|คีย์|api key/i);
  });

  it("models_unavailable: its own sentence, no admin-key instruction", async () => {
    mockApi.getKnowledgeAvailability.mockResolvedValue({ available: false, reason: "models_unavailable" });
    render(<KnowledgeBasePage />);
    const notice = await screen.findByTestId("kb-unavailable");
    expect(th["kb.unavailable.models_unavailable"]).toBeTruthy();
    expect(notice).toHaveTextContent(th["kb.unavailable.models_unavailable"]);
    expect(notice.textContent).not.toMatch(/openai|คีย์|api key/i);
  });

  it("no knowledge-base string in either language tells anyone to fix an OpenAI key", () => {
    for (const dict of [th, en]) {
      for (const [key, value] of Object.entries(dict)) {
        if (!key.startsWith("kb.")) continue;
        expect(`${key}: ${value}`).not.toMatch(/openai/i);
      }
    }
  });
});

describe("FIX132 — loading ends by itself, so the page asks again by itself", () => {
  it("the knowledge page flips from 'starting' to usable without a reload", async () => {
    expect(recheck).toBeDefined();
    recheck!.loadingMs = 20;
    mockApi.getKnowledgeAvailability
      .mockResolvedValueOnce({ available: false, reason: "models_loading" })
      .mockResolvedValue({ available: true });
    render(<KnowledgeBasePage />);
    expect(await screen.findByTestId("kb-unavailable")).toBeInTheDocument();
    await act(async () => { await new Promise((r) => setTimeout(r, 80)); });
    await flush();
    expect(screen.queryByTestId("kb-unavailable")).toBeNull();
    expect(await screen.findByText(th["kb.create.full"])).toBeInTheDocument();
  });

  it("the composer's knowledge-base affordance appears once the models are ready", async () => {
    expect(recheck).toBeDefined();
    recheck!.loadingMs = 20;
    mockApi.getKnowledgeAvailability
      .mockResolvedValueOnce({ available: false, reason: "models_loading" })
      .mockResolvedValue({ available: true });
    function Probe() {
      return <span data-testid="probe">{useKnowledgeAvailable() ? "yes" : "no"}</span>;
    }
    render(<Probe />);
    await flush();
    expect(screen.getByTestId("probe")).toHaveTextContent("no");
    await act(async () => { await new Promise((r) => setTimeout(r, 80)); });
    expect(screen.getByTestId("probe")).toHaveTextContent("yes");
  });

  it("models_unavailable is not polled (a failure does not end by itself)", async () => {
    expect(recheck).toBeDefined();
    recheck!.loadingMs = 20;
    mockApi.getKnowledgeAvailability.mockResolvedValue({ available: false, reason: "models_unavailable" });
    render(<KnowledgeBasePage />);
    await screen.findByTestId("kb-unavailable");
    await act(async () => { await new Promise((r) => setTimeout(r, 80)); });
    expect(mockApi.getKnowledgeAvailability).toHaveBeenCalledTimes(1);
  });
});
