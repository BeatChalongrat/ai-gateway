// FIX127 A4 — a failed turn shows ONE reason.
//
// R3: the card said "ข้อความยาวเกินไป" in the red notice and "บริการ AI
// ขัดข้อง" in the action row under it — two reasons, one of them wrong.
// The label yields whenever a notice (live frame or reloaded row) renders.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MessageBubble } from "./MessageBubble";

const initial = useChatStore.getState();
beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
});
afterEach(cleanup);

function msg(extra: Partial<Message>): Message {
  return { id: "a1", role: "assistant", content: "", status: "completed", created_at: "2026-01-01T00:00:00Z", ...extra };
}

describe("FIX127 A4 — one reason per failed card", () => {
  it("live frame: message_too_long notice, no 'AI service error' label", () => {
    render(<MessageBubble convId="c1" message={msg({
      status: "error", terminal_cause: "error", error_code: "message_too_long",
      error_message: dictionaries.th["error.message_too_long"],
    })} />);
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.th["error.message_too_long"]);
    expect(screen.queryByTestId("error-label")).toBeNull();
    expect(screen.queryByText(dictionaries.th["msg.aiError"])).toBeNull();
  });

  it("reloaded row with a code: one notice, no label", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", terminal_cause: "error", error_code: "all_models_failed" })} />);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByTestId("error-label")).toBeNull();
  });

  it("reloaded row with partial text and no code: the label is the one reason", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", content: "partial answer", terminal_cause: "error" })} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("error-label").textContent).toBe(dictionaries.th["msg.aiError"]);
  });
});
