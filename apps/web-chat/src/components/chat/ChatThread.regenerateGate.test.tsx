// FIX100 Part B — the gate itself, where the decision is actually made.
//
// regenerateGate.test.tsx pins the RULE (knownNotRetryable) and the way
// forward that replaces the button. This one renders the real ChatThread
// and asks the only question that matters to the person: is the button
// there? Before this round the answer was "always", whatever had failed.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("./MessageBubble", () => ({
  MessageBubble: ({ message, showRegenerate }: { message: { id: string }; showRegenerate?: boolean }) =>
    showRegenerate ? <button data-testid={`regen-${message.id}`}>regenerate</button> : <div />,
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";

import { ChatThread } from "./ChatThread";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

function thread(last: Partial<Message>): void {
  const messages: Message[] = [
    { id: "u1", role: "user", content: "ถามหน่อย", status: "completed", created_at: "2026-09-10T07:55:03Z" } as Message,
    { id: "a1", role: "assistant", content: "", status: "error", created_at: "2026-09-10T07:58:41Z", ...last } as Message,
  ];
  render(<ChatThread convId="c1" messages={messages} onRegenerate={() => {}} />);
}

describe("Regenerate on the last assistant row", () => {
  it("is gone for the failure that failed the owner six times", () => {
    // deepseek, HTTP 400, retryable:false. Regenerate would have sent the
    // identical request for a seventh time.
    thread({ error_code: "upstream_bad_response", error_retryable: false });
    expect(screen.queryByTestId("regen-a1")).toBeNull();
  });

  it("is still there when a retry is exactly the right answer", () => {
    thread({ error_code: "provider_timeout", error_retryable: true });
    expect(screen.getByTestId("regen-a1")).toBeTruthy();
  });

  it("is still there for a failure read back from the server after a reload", () => {
    // No error frame survives a reload, so there is no code to judge by.
    // The button must not vanish from every failed turn in the history.
    thread({});
    expect(screen.getByTestId("regen-a1")).toBeTruthy();
  });

  it("is still there on a reply that simply finished", () => {
    thread({ content: "เรียบร้อยครับ", status: "completed" });
    expect(screen.getByTestId("regen-a1")).toBeTruthy();
  });
});
