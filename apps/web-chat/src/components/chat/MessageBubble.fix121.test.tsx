// FIX121 C5 — a failed turn read back from the server lists what was tried.
//
// The page has two error cards: the live one (fed by the SSE error frame)
// and the one for a row hydrated after a reload or on another device. Only
// the live one was ever given the attempt log, because until migration 047
// only the live frame had it. Found by driving the page after FIX121: the
// row carried error_attempts and the reloaded card still showed none.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { useI18nStore } from "@/lib/i18n";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

const attempts = [
  { provider: "sranai", model: "qwen3.8-27b-fp8", error_code: "provider_unavailable" },
  { provider: "openai", model: "gpt-5-mini", error_code: "rate_limit_exceeded" },
  { provider: "openai", model: "gpt-5-mini-2025-08-07", error_code: "rate_limit_exceeded" },
];

function stored(partial: Partial<Message>): Message {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    created_at: "2026-09-17T06:54:00Z",
    status: "error",
    error_code: "all_models_failed",
    terminal_cause: "error",
    run_id: "fix121-B-after2-1789628037",
    ...partial,
  } as Message;
}

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("FIX121 — the reloaded error card lists the attempts", () => {
  it("a stored failed row with error_attempts shows 'Connection attempts (3)' and each model", () => {
    render(<MessageBubble message={stored({ error_attempts: attempts })} convId="c1" />);
    const summary = screen.getByText("Connection attempts (3)");
    fireEvent.click(summary);
    const items = [...summary.closest("details")!.querySelectorAll("li")].map((li) => li.textContent ?? "");
    expect(items).toHaveLength(3);
    attempts.forEach((a, i) => expect(items[i].startsWith(`${a.model} — `)).toBe(true));
    expect(screen.getByText("fix121-B-after2-1789628037")).toBeTruthy();
  });

  it("a stored failed row without attempts (pre-047, or a failure that tried one model) shows no list", () => {
    render(<MessageBubble message={stored({})} convId="c1" />);
    expect(screen.queryByText(/Connection attempts/)).toBeNull();
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});
