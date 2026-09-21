// FIX124 — what the bubble says when a turn did not simply finish.
//   R2  a lost connection is a state of the link: "reconnecting", then
//       "load the answer" — never the red "AI service error" label.
//   R3  a turn that stopped at the tool-step limit says so, with the number
//       of steps when the live frame carried it, and offers to continue.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MessageBubble } from "./MessageBubble";

const msg = (over: Partial<Message>): Message =>
  ({ id: "m1", role: "assistant", content: "Let me work on it.", status: "completed", created_at: "2026-09-21T00:00:00Z", ...over }) as Message;
const initial = useChatStore.getState();

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
});
afterEach(cleanup);

describe("R3 — the tool-step limit", () => {
  it("says how many steps ran and offers Continue, which sends an ordinary message", () => {
    const sendMessage = vi.fn(async () => {});
    useChatStore.setState({ sendMessage });
    render(<MessageBubble convId="c1" message={msg({ stop_reason: "tool_use", tool_rounds: 5 })} showRegenerate onRegenerate={() => {}} />);
    const note = screen.getByTestId("stop-reason-notice");
    expect(note.getAttribute("data-stop-reason")).toBe("tool_use");
    expect(note.textContent).toContain(dictionaries.th["msg.stopToolLimit"].replace("{n}", "5").slice(0, 20));
    fireEvent.click(screen.getByTestId("continue-button"));
    expect(sendMessage).toHaveBeenCalledWith("c1", dictionaries.th["msg.continuePrompt"]);
  });

  it("after a reload the number is gone, the notice is not", () => {
    render(<MessageBubble convId="c1" message={msg({ stop_reason: "tool_use" })} showRegenerate onRegenerate={() => {}} />);
    expect(screen.getByTestId("stop-reason-notice").textContent).toContain(dictionaries.th["msg.stopToolLimitNoCount"].slice(0, 20));
  });

  it("no Continue on an older reply — only where the conversation can go on from", () => {
    render(<MessageBubble convId="c1" message={msg({ stop_reason: "tool_use", tool_rounds: 5 })} />);
    expect(screen.getByTestId("stop-reason-notice")).toBeTruthy();
    expect(screen.queryByTestId("continue-button")).toBeNull();
  });

  it("an ordinary finished answer says nothing", () => {
    render(<MessageBubble convId="c1" message={msg({ stop_reason: "end_turn" })} />);
    expect(screen.queryByTestId("stop-reason-notice")).toBeNull();
  });
});

describe("R2 — a lost connection", () => {
  it("waiting: 'reconnecting', no error label", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "streaming", isStreaming: true, reconnecting: true })} />);
    expect(screen.getByTestId("reconnecting-notice").textContent).toBe(dictionaries.th["msg.reconnecting"]);
    expect(screen.queryByTestId("error-label")).toBeNull();
  });

  // The bubble is memoized field by field. These states change on a row whose
  // content and status do not move, so a fresh render (the tests above)
  // cannot show what the live page did: the notice set and never drawn.
  it("appears and disappears on a MOUNTED bubble whose content and status did not change", () => {
    const base = msg({ status: "streaming", isStreaming: true, content: "half an answer" });
    const { rerender } = render(<MessageBubble convId="c1" message={base} />);
    expect(screen.queryByTestId("reconnecting-notice")).toBeNull();
    rerender(<MessageBubble convId="c1" message={{ ...base, reconnecting: true }} />);
    expect(screen.getByTestId("reconnecting-notice")).toBeTruthy();
    rerender(<MessageBubble convId="c1" message={{ ...base, isStreaming: false, awaiting_reload: true }} />);
    expect(screen.queryByTestId("reconnecting-notice")).toBeNull();
    expect(screen.getByTestId("awaiting-reload-notice")).toBeTruthy();
    rerender(<MessageBubble convId="c1" message={base} />);
    expect(screen.queryByTestId("awaiting-reload-notice")).toBeNull();
  });

  it("the wait ran out: 'load the answer' calls reloadAnswer; still no error label", () => {
    const reloadAnswer = vi.fn(async () => {});
    useChatStore.setState({ reloadAnswer });
    render(<MessageBubble convId="c1" message={msg({ status: "streaming", isStreaming: false, awaiting_reload: true })} />);
    expect(screen.getByTestId("awaiting-reload-notice").textContent).toContain(dictionaries.th["msg.awaitingReload"]);
    fireEvent.click(screen.getByTestId("load-answer-button"));
    expect(reloadAnswer).toHaveBeenCalledWith("c1");
    expect(screen.queryByTestId("error-label")).toBeNull();
  });

  it("a turn that ended because the link broke (desktop) is labelled 'connection lost', not 'AI service error'", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", content: "half", terminal_cause: "disconnected", error_code: "stream_interrupted" })} />);
    expect(screen.getByTestId("error-label").textContent).toBe(dictionaries.th["msg.connectionLost"]);
  });

  it("a real failure keeps its label", () => {
    render(<MessageBubble convId="c1" message={msg({ status: "error", content: "", terminal_cause: "error", error_code: "all_models_failed" })} />);
    expect(screen.getByTestId("error-label").textContent).toBe(dictionaries.th["msg.aiError"]);
  });
});
