// FIX49 Part A — a failed turn hydrated from the server must explain
// itself. The live SSE error frame is not persisted, so a reload used to
// show a blank assistant bubble with only the tiny status label; now the
// bubble carries the localized red notice, and regenerate is the retry.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
// Voice output reaches for speechSynthesis, which jsdom lacks.
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message, ModelEntry } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";
import { useChatStore } from "@/stores/chatStore";
import { outputLimitAlternative } from "@/lib/outputLimitAlternative";

import { MessageBubble } from "./MessageBubble";

function msg(partial: Partial<Message>): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    created_at: new Date().toISOString(),
    status: "completed",
    ...partial,
  } as Message;
}

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});
afterEach(cleanup);

const model = (id: string, overrides: Partial<ModelEntry> = {}): ModelEntry => ({
  id, name: id, provider: "vendor", description: "", context_window: 32000,
  capabilities: ["function_calling", "vision"], allowed: true, icon: "", category: "fast",
  ...overrides,
});

describe("output-limit model switch", () => {
  const failed = model("test-own", { provider: "own" });
  const alternative = model("test-alternative", { name: "Available Model" });

  it("offers an allowed visible chat model preserving tools and images", () => {
    const rejected = [
      model("hidden", { show_in_chat: false }), model("denied", { allowed: false }),
      model("media", { model_kind: "video" }), model("no-tools", { capabilities: ["vision"] }),
      model("no-images", { capabilities: ["function_calling"] }),
    ];
    expect(outputLimitAlternative(failed.id, [failed, ...rejected])).toBeNull();
    expect(outputLimitAlternative(failed.id, [failed, ...rejected, alternative])).toEqual(alternative);
    expect(outputLimitAlternative("unknown", [alternative])).toBeNull();
  });

  it("prefers a different provider without hardcoded model names", () => {
    const same = model("same-provider", { provider: "own" });
    expect(outputLimitAlternative(failed.id, [failed, same, alternative])).toEqual(alternative);
    expect(outputLimitAlternative(failed.id, [failed, same])).toEqual(same);
  });

  it.each(["en", "th"] as const)("%s: switches this conversation only when clicked", async (locale) => {
    useI18nStore.setState({ locale });
    useModelStore.setState({ models: [failed, alternative] });
    const change = vi.spyOn(useChatStore.getState(), "setConversationModel").mockResolvedValue(undefined);
    render(<MessageBubble convId="c1" message={msg({
      model_id: failed.id, status: "error", error_code: "output_limit_reached",
      error_message: dictionaries[locale]["error.output_limit_reached"],
    })} />);
    expect(screen.getByRole("alert").textContent).toContain(dictionaries[locale]["error.output_limit_reached"]);
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: dictionaries[locale]["error.switchModel"].replace("{model}", alternative.name) }));
    await waitFor(() => expect(change).toHaveBeenCalledWith("c1", alternative.id));
    change.mockRestore();
  });
});

describe("failed turn on reload (FIX49 Part A)", () => {
  it("an errored assistant message with empty content shows the explanation notice", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", content: "" })} />);
    expect(screen.getByText(dictionaries.en["msg.turnFailedReload"])).toBeTruthy();
  });

  it("a live error frame keeps its own message — no duplicate generic notice", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ status: "error", content: "", error_message: "Real reason from SSE" })}
      />,
    );
    expect(screen.getByText("Real reason from SSE")).toBeTruthy();
    expect(screen.queryByText(dictionaries.en["msg.turnFailedReload"])).toBeNull();
  });

  it("an errored turn that still produced partial text does not get the empty-bubble notice", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", content: "partial answer…" })} />);
    expect(screen.queryByText(dictionaries.en["msg.turnFailedReload"])).toBeNull();
  });

  it("a completed message never shows it", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "4" })} />);
    expect(screen.queryByText(dictionaries.en["msg.turnFailedReload"])).toBeNull();
  });

  it("Thai text exists", () => {
    expect(dictionaries.th["msg.turnFailedReload"]).toBeTruthy();
  });
});
