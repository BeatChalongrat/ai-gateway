// FIX120 — the "answered by X" notice is not a session-only thing: a row
// that carries fallback_from (migration 046) beside model_id shows the same
// line after a reload as the live fallback_info frame did.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble, fallbackNoticeFor } from "./MessageBubble";

function msg(over: Partial<Message>): Message {
  return { id: "m1", role: "assistant", content: "สวัสดี", status: "completed", created_at: "2026-09-15T00:00:00Z", ...over } as Message;
}

const expected = (locale: "th" | "en") =>
  dictionaries[locale]["error.fallbackNotice"].replace("{actual}", "qwen3.8-27b-fp8").replace("{requested}", "gpt-5.6-luna");

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("FIX120 — the fallback notice comes back from the row", () => {
  it("a reloaded row with fallback_from + model_id shows the notice (TH)", () => {
    render(<MessageBubble convId="c1" message={msg({ fallback_from: "gpt-5.6-luna", model_id: "qwen3.8-27b-fp8" })} />);
    const note = screen.getByTestId("fallback-notice");
    expect(note.textContent).toBe(expected("th"));
    expect(note.getAttribute("data-requested-model")).toBe("gpt-5.6-luna");
    expect(note.getAttribute("data-actual-model")).toBe("qwen3.8-27b-fp8");
  });

  it("the same row in English", () => {
    useI18nStore.setState({ locale: "en" });
    render(<MessageBubble convId="c1" message={msg({ fallback_from: "gpt-5.6-luna", model_id: "qwen3.8-27b-fp8" })} />);
    expect(screen.getByTestId("fallback-notice").textContent).toBe(expected("en"));
  });

  it("the live frame's local fallback_info still wins and renders the same line", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ fallback_info: { requested_model: "gpt-5.6-luna", actual_model: "qwen3.8-27b-fp8" }, model_id: "qwen3.8-27b-fp8" })}
      />,
    );
    expect(screen.getByTestId("fallback-notice").textContent).toBe(expected("th"));
  });

  it("no notice when the requested model answered, or when nothing says otherwise", () => {
    render(<MessageBubble convId="c1" message={msg({ model_id: "gpt-5.6-luna" })} />);
    expect(screen.queryByTestId("fallback-notice")).toBeNull();
    cleanup();
    render(<MessageBubble convId="c1" message={msg({ fallback_from: "gpt-5.6-luna", model_id: "gpt-5.6-luna" })} />);
    expect(screen.queryByTestId("fallback-notice")).toBeNull();
    cleanup();
    render(<MessageBubble convId="c1" message={msg({ fallback_from: "gpt-5.6-luna" })} />);
    expect(screen.queryByTestId("fallback-notice")).toBeNull();
  });

  it("fallbackNoticeFor is the one rule", () => {
    expect(fallbackNoticeFor({})).toBeNull();
    expect(fallbackNoticeFor({ fallback_from: "a", model_id: "a" })).toBeNull();
    expect(fallbackNoticeFor({ fallback_from: "a", model_id: "b" })).toEqual({ requested_model: "a", actual_model: "b" });
    expect(fallbackNoticeFor({ fallback_info: { requested_model: "x", actual_model: "y" }, fallback_from: "a", model_id: "b" })).toEqual({ requested_model: "x", actual_model: "y" });
  });
});
