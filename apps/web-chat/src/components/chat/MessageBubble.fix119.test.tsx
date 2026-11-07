// FIX119 — the card shows a reference id a person can copy, and a completed
// answer that was cut off (or refused / filtered) says so under the text.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCopy, mockToast } = vi.hoisted(() => ({ mockCopy: vi.fn(async () => true), mockToast: vi.fn() }));
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: mockCopy }));
vi.mock("@/components/ui/Toast", () => ({ toast: mockToast }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

const RUN = "5f3c1a2b-0000-4000-8000-0123456789ab";
function msg(over: Partial<Message>): Message {
  return { id: "m1", role: "assistant", content: "", status: "completed", created_at: "2026-09-15T00:00:00Z", ...over } as Message;
}

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  mockCopy.mockClear();
  mockToast.mockClear();
});
afterEach(cleanup);

describe("FIX119 — reference id on the error card", () => {
  it("shows the run id and copies it", async () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", error_code: "provider_timeout", error_message: "x", run_id: RUN })} />);
    const ref = screen.getByTestId("error-reference");
    expect(ref.textContent).toContain(dictionaries.th["error.referenceId"]);
    expect(ref.textContent).toContain(RUN);
    fireEvent.click(screen.getByRole("button", { name: dictionaries.th["error.copyReference"] }));
    await waitFor(() => expect(mockCopy).toHaveBeenCalledWith(RUN));
    expect(mockToast).toHaveBeenCalledWith(dictionaries.th["error.referenceCopied"]);
  });

  it("a reloaded failed row with a code and a run id shows both", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", error_code: "stream_interrupted", run_id: RUN })} />);
    expect(screen.getByRole("alert").textContent).toContain(dictionaries.th["error.stream_interrupted"]);
    expect(screen.getByTestId("error-reference").textContent).toContain(RUN);
  });

  it("no run id, no reference line", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", error_code: "provider_timeout", error_message: "x" })} />);
    expect(screen.queryByTestId("error-reference")).toBeNull();
  });
});

describe("FIX119 — the stop-reason notice", () => {
  it("max_tokens: the text stands and the person is told it is cut off (TH)", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "ครึ่งหนึ่งของ", stop_reason: "max_tokens" })} />);
    const note = screen.getByTestId("stop-reason-notice");
    expect(note.getAttribute("data-stop-reason")).toBe("max_tokens");
    expect(note.textContent).toBe(dictionaries.th["msg.stopMaxTokens"]);
    expect(screen.queryByRole("alert")).toBeNull(); // not an error
  });

  it("refusal is a note, not an error; end_turn and unknown say nothing", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "no", stop_reason: "refusal" })} />);
    expect(screen.getByTestId("stop-reason-notice").textContent).toBe(dictionaries.th["msg.stopRefusal"]);
    cleanup();
    render(<MessageBubble convId="c1" message={msg({ content: "fine", stop_reason: "end_turn" })} />);
    expect(screen.queryByTestId("stop-reason-notice")).toBeNull();
    cleanup();
    render(<MessageBubble convId="c1" message={msg({ content: "fine", stop_reason: "unknown" })} />);
    expect(screen.queryByTestId("stop-reason-notice")).toBeNull();
  });

  it("a streaming or failed row never shows the notice", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "…", stop_reason: "max_tokens", status: "streaming", isStreaming: true })} />);
    expect(screen.queryByTestId("stop-reason-notice")).toBeNull();
  });
});
