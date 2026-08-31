// Phase 11 Task 3 — model metadata in the picker + the Thinking-Mode toggle.
//
// Both of these existed end-to-end EXCEPT for the last mile: the catalog has
// always served description/context/pricing (handler/model.go) and the store
// has always had setConversationReasoning — neither had any UI. These tests
// pin the wiring.
//
// Phase 13.1 narrowed "metadata" to the DESCRIPTION only ("internal tool on
// a shared account — pricing was noise"). FIX33 B1 reinstates the meta line,
// quieter: credits are already visible in the header and the usage panel
// bills per model, and the context window is what the FIX33 context meter
// budgets against. The assertions below now pin that the line renders when
// the catalog sends the numbers and stays absent when it doesn't.

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
import { ModelSelector, isFillerDescription } from "./ModelSelector";

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

  it("FIX35 Part A — context window and price are hover-only: in the row tooltip, never a permanent line", () => {
    // model() carries context_window: 128000 and $2.50 / $10.00.
    openPicker([model()]);

    // The FIX33 B1 permanent meta line is gone (Phase 13.1's call, confirmed).
    expect(screen.queryByTestId("model-meta")).toBeNull();
    expect(screen.queryByText(/per 1M/i)).toBeNull();
    // …but the numbers are still one hover away, on the name.
    const names = screen.getAllByTestId("model-row-name").filter((n) => n.textContent === "GPT-4o");
    expect(names.length).toBeGreaterThan(0);
    const title = names[0].getAttribute("title") ?? "";
    expect(title).toContain("128K");
    expect(title).toContain("$2.5 / $10");
    expect(title).toContain("per 1M");
  });

  it("renders no meta line and no tooltip numbers when the catalog sends none", () => {
    openPicker([model({ id: "bare", name: "Bare", description: "", context_window: 0, input_price_per_1m: undefined, output_price_per_1m: undefined })]);

    expect(screen.getAllByText("Bare").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("model-meta")).toBeNull();
    expect(screen.queryByText(/per 1M/i)).toBeNull();
    const name = screen.getAllByTestId("model-row-name").find((n) => n.textContent === "Bare");
    expect(name?.getAttribute("title") ?? "").not.toMatch(/per 1M|K context/);
  });

  it("FIX35 Part A — a row is name + provider: the provider line stays, filler descriptions do not", () => {
    openPicker([
      model({ id: "filler", name: "GPT 5.6 Terra", provider: "openai", description: "OpenAI chat model." }),
      model({ id: "filler2", name: "Claude X", provider: "anthropic", description: "Anthropic Claude model." }),
      model({ id: "useful", name: "GPT-4o Useful", description: "OpenAI's most capable multimodal flagship. Great for complex tasks, vision, and tool use." }),
    ]);

    // Provider line is kept — we are a multi-provider gateway.
    expect(screen.getAllByText("openai").length).toBeGreaterThan(0);
    expect(screen.getAllByText("anthropic").length).toBeGreaterThan(0);
    // The "<Provider> model." placeholder is hidden; real copy is shown.
    expect(screen.queryByText("OpenAI chat model.")).toBeNull();
    expect(screen.queryByText("Anthropic Claude model.")).toBeNull();
    expect(screen.getAllByText(/most capable multimodal flagship/).length).toBeGreaterThan(0);
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

// FIX35 Part A — the filler rule is structural (a real sentence of ≥5 words
// that is not the "<Provider> [<family>] model." template), not a list of
// model ids. Pin both sides so a future placeholder can't sneak back in and
// real copy can't be swallowed.
describe("isFillerDescription", () => {
  it("hides chat-edge's provider placeholders", () => {
    for (const s of [
      "OpenAI chat model.",
      "Anthropic Claude model.",
      "Google Gemini model.",
      "Meta Llama model.",
      "Mistral model.",
      "",
      "   ",
      "Fast model",
    ]) {
      expect(isFillerDescription(s), s).toBe(true);
    }
  });

  it("keeps descriptions that say something", () => {
    for (const s of [
      "OpenAI's most capable multimodal flagship. Great for complex tasks, vision, and tool use.",
      "Cheaper, faster GPT-4o variant. Good default for everyday chat.",
      "Reasoning-tuned model that thinks before responding. Best for hard math/code.",
      "Fast multimodal flagship for everyday work",
    ]) {
      expect(isFillerDescription(s), s).toBe(false);
    }
  });
});
