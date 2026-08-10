// Phase 11 Task 3 — model metadata in the picker + the Thinking-Mode toggle.
//
// Both of these existed end-to-end EXCEPT for the last mile: the catalog has
// always served description/context/pricing (handler/model.go) and the store
// has always had setConversationReasoning — neither had any UI. These tests
// pin the wiring.
//
// Phase 13.1 narrowed "metadata" to the DESCRIPTION only. SRAN is an internal
// tool on a shared account, so context windows and per-token pricing were
// noise nobody acts on. The catalog still sends those fields — the assertions
// below now pin that we do NOT render them, so a future change that reinstates
// the meta line fails here rather than quietly shipping prices to users.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    setConversationReasoning: vi.fn(),
    uploadFile: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0,
  }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({
    isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0,
  }),
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation, ModelEntry } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";
import { ModelSelector } from "./ModelSelector";

const chatInitial = useChatStore.getState();

function model(extra?: Partial<ModelEntry>): ModelEntry {
  return {
    id: "gpt-4o",
    name: "GPT-4o",
    provider: "openai",
    description: "Fast multimodal flagship for everyday work",
    context_window: 128000,
    capabilities: [],
    icon: "",
    category: "flagship",
    allowed: true,
    input_price_per_1m: 2.5,
    output_price_per_1m: 10,
    ...extra,
  };
}

function conv(extra?: Partial<Conversation>): Conversation {
  return {
    id: "c1",
    title: "Conv",
    model_id: "gpt-4o",
    message_count: 1,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

// ─── the picker ─────────────────────────────────────────────────

function openPicker(models: ModelEntry[]) {
  useModelStore.setState({ models, loaded: true, loading: false });
  render(<ModelSelector activeModelId={AUTO_MODEL_ID} onPick={vi.fn()} />);
  fireEvent.click(screen.getByRole("button"));
}

describe("ModelSelector metadata", () => {
  it("renders the description the catalog sends", () => {
    openPicker([model()]);

    expect(
      screen.getAllByText("Fast multimodal flagship for everyday work").length,
    ).toBeGreaterThan(0);
  });

  it("never renders context window or pricing, even when the catalog sends them", () => {
    // model() carries context_window: 128000 and $2.50 / $10.00 — the fields
    // are present and deliberately unrendered (Phase 13.1).
    openPicker([model()]);

    expect(screen.queryByText(/context/i)).toBeNull();
    expect(screen.queryByText(/per 1M/i)).toBeNull();
    expect(screen.queryByText(/\$/)).toBeNull();
    expect(screen.queryByText(/128K/)).toBeNull();
  });

  it("renders nothing extra for a model with no description", () => {
    openPicker([model({ id: "bare", name: "Bare", description: "" })]);

    expect(screen.getAllByText("Bare").length).toBeGreaterThan(0);
    expect(screen.queryByText(/context/i)).toBeNull();
    expect(screen.queryByText(/per 1M/i)).toBeNull();
  });

  it("keeps the existing picker behaviour — Auto row still there", () => {
    openPicker([model()]);
    expect(
      screen.getAllByText(dictionaries.en["model.auto.name"]).length,
    ).toBeGreaterThan(0);
  });
});

// ─── Thinking-Mode toggle ───────────────────────────────────────

function mountComposer(activeModelId: string) {
  render(
    <ChatInput
      conversationId="c1"
      disabled={false}
      streaming={false}
      activeModelId={activeModelId}
      onSend={vi.fn()}
      onStop={vi.fn()}
    />,
  );
}

describe("Thinking-Mode toggle", () => {
  const reasoningModel = model({
    id: "o3",
    name: "o3",
    reasoning: { supported: true, modes: ["auto"], default_mode: "auto", summary_supported: true },
  });

  it("appears for a reasoning-capable model and calls the store action", async () => {
    useModelStore.setState({ models: [reasoningModel], loaded: true, loading: false });
    useChatStore.setState({ conversations: [conv({ model_id: "o3" })] });
    mockApi.setConversationReasoning.mockResolvedValue(conv({ reasoning_enabled: true }));

    mountComposer("o3");
    const btn = screen.getByLabelText(dictionaries.en["input.reasoning"]);
    expect(btn).toHaveAttribute("aria-pressed", "false");

    await act(async () => {
      fireEvent.click(btn);
    });

    expect(mockApi.setConversationReasoning).toHaveBeenCalledWith("c1", {
      reasoning_enabled: true,
    });
  });

  it("reflects the conversation's saved state", () => {
    useModelStore.setState({ models: [reasoningModel], loaded: true, loading: false });
    useChatStore.setState({
      conversations: [conv({ model_id: "o3", reasoning_enabled: true })],
    });

    mountComposer("o3");

    expect(screen.getByLabelText(dictionaries.en["input.reasoning"])).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("is hidden for a model that cannot reason", () => {
    useModelStore.setState({ models: [model()], loaded: true, loading: false });
    useChatStore.setState({ conversations: [conv()] });

    mountComposer("gpt-4o");

    expect(screen.queryByLabelText(dictionaries.en["input.reasoning"])).toBeNull();
  });

  it("is hidden on Auto — the router may pick a non-reasoning model", () => {
    useModelStore.setState({ models: [reasoningModel], loaded: true, loading: false });
    useChatStore.setState({ conversations: [conv()] });

    mountComposer(AUTO_MODEL_ID);

    expect(screen.queryByLabelText(dictionaries.en["input.reasoning"])).toBeNull();
  });

  it("localizes to Thai", () => {
    act(() => {
      useI18nStore.setState({ locale: "th" });
    });
    useModelStore.setState({ models: [reasoningModel], loaded: true, loading: false });
    useChatStore.setState({ conversations: [conv({ model_id: "o3" })] });

    mountComposer("o3");

    expect(
      screen.getByLabelText(dictionaries.th["input.reasoning"]),
    ).toBeInTheDocument();
    expect(dictionaries.th["input.reasoning"]).not.toBe(
      dictionaries.en["input.reasoning"],
    );
  });
});
