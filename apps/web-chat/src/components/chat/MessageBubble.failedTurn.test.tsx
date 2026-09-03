// FIX49 Part A — a failed turn hydrated from the server must explain
// itself. The live SSE error frame is not persisted, so a reload used to
// show a blank assistant bubble with only the tiny status label; now the
// bubble carries the localized red notice, and regenerate is the retry.

import { cleanup, render, screen } from "@testing-library/react";
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
import type { Message } from "@/lib/types";

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
