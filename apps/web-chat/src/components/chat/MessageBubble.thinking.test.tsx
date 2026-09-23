// FIX116 Part B — the ThinkingBlock header follows the ROW's state, not a
// flag that nothing cleared. A cancelled or failed turn whose is_thinking
// was left on must read "Analyzed", never "Thinking…"; the reasoning text
// it already received stays on screen.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
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

const THINKING = dictionaries.en["reasoning.thinking"];
const ANALYZED = dictionaries.en["reasoning.analyzed"];

describe("FIX116 Part B — thinking header on a finished row", () => {
  it.each([
    ["cancelled", "cancelled"],
    ["error", "error"],
    ["completed", "completed"],
  ] as const)("a %s row with is_thinking still on reads Analyzed and keeps the reasoning text", (_label, status) => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ status, isStreaming: false, is_thinking: true, reasoning_content: "step one", content: status === "completed" ? "done" : "" })}
      />,
    );
    expect(screen.queryByText(THINKING)).toBeNull();
    expect(screen.getByText(ANALYZED)).toBeTruthy();
    // FIX130 B2 — a finished row mounts collapsed; the text is kept
    // behind the header, one click away.
    fireEvent.click(screen.getByText(ANALYZED).closest("button") as HTMLButtonElement);
    expect(screen.getByText("step one")).toBeTruthy();
  });

  it("a row that is streaming and thinking still reads Thinking…", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "streaming", isStreaming: true, is_thinking: true, reasoning_content: "step one" })} />);
    expect(screen.getByText(THINKING)).toBeTruthy();
    expect(screen.queryByText(ANALYZED)).toBeNull();
  });
});
