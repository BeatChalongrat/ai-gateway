// FIX100 Part B — Regenerate is offered only when a retry could work.
//
// The owner's conversation e493ca28 failed six times in a row. Three of
// those were deepseek answering
//
//   {"code":"upstream_bad_response","retryable":false,
//    "message":"The AI provider rejected the request. Change the request
//               before sending it again.", ...}
//
// and directly under that sentence sat a Regenerate button, which sends
// the identical request. The button never consulted the error at all: the
// only things it looked at were "is this the last assistant row" and "is
// nothing streaming". The retryability table it needed has been in
// errorMessages.ts since FIX47.
//
// The gate is deliberately three-valued and its default is to LEAVE THE
// BUTTON — the tests below spend most of their length on that, because a
// rule that removes a working retry from every reloaded thread would be a
// worse bug than the one being fixed.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import { knownNotRetryable, RETRYABLE_CODES } from "@/lib/errorMessages";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("knownNotRetryable", () => {
  it("is true for the code that failed the owner's turn", () => {
    // Astra's call 7, verbatim from the reproduction ledger.
    expect(knownNotRetryable("upstream_bad_response", false)).toBe(true);
    // ...and by the table alone, when the frame carried no flag.
    expect(knownNotRetryable("upstream_bad_response")).toBe(true);
  });

  it("is false for every code the retry table calls retryable", () => {
    for (const code of RETRYABLE_CODES) {
      expect(knownNotRetryable(code)).toBe(false);
    }
  });

  it("keeps the button when it does not know — the important half", () => {
    // A row read back from the server after a reload: the error frame is
    // live-only, so there is no code and no flag. Silence must not cost
    // the person their retry.
    expect(knownNotRetryable(undefined, undefined)).toBe(false);
    // A code newer than this build's table.
    expect(knownNotRetryable("some_code_shipped_after_this_build")).toBe(false);
  });

  it("lets the gateway's own flag win over the table, both ways", () => {
    // A code the table calls permanent, which this failure says is not.
    expect(knownNotRetryable("upstream_bad_response", true)).toBe(false);
    // A code the table calls transient, which this failure says is not.
    expect(knownNotRetryable("provider_error", false)).toBe(true);
  });

  it("agrees with the sentence the user is reading", () => {
    // The sentence the person actually reads is the DICTIONARY's, not the
    // gateway catalogue's — localizeErrorCode returns the dictionary entry
    // for any code it knows. FIX94 reworded this one to name both ways out,
    // and it already tells them to write a different message; a Regenerate
    // button under it contradicts the only instruction on screen.
    expect(dictionaries.en["error.upstream_bad_response"]).toMatch(/change the message or the model/i);
    expect(dictionaries.th["error.upstream_bad_response"]).toMatch(/เปลี่ยน/);
    expect(knownNotRetryable("upstream_bad_response", false)).toBe(true);
  });
});

function failed(partial: Partial<Message> = {}): Message {
  return {
    id: "m1",
    role: "assistant",
    content: "",
    created_at: new Date().toISOString(),
    status: "error",
    error_message: "The AI provider rejected the request. Change the request before sending it again.",
    ...partial,
  } as Message;
}

describe("the way forward when Regenerate is taken away", () => {
  it("offers a new message instead, in place of the retry", () => {
    render(
      <MessageBubble
        convId="c1"
        message={failed({ error_code: "upstream_bad_response", error_retryable: false })}
        showRegenerate={false}
      />,
    );
    expect(screen.getByTestId("new-message-instead")).toHaveTextContent(
      dictionaries.en["error.sendNewMessage"],
    );
  });

  it("says it in Thai too", () => {
    useI18nStore.setState({ locale: "th" });
    render(
      <MessageBubble
        convId="c1"
        message={failed({ error_code: "upstream_bad_response", error_retryable: false })}
        showRegenerate={false}
      />,
    );
    expect(screen.getByTestId("new-message-instead")).toHaveTextContent(
      dictionaries.th["error.sendNewMessage"],
    );
  });

  it("does not appear on a failure a retry can fix", () => {
    render(
      <MessageBubble
        convId="c1"
        message={failed({ error_code: "provider_timeout", error_retryable: true })}
        showRegenerate
      />,
    );
    expect(screen.queryByTestId("new-message-instead")).toBeNull();
  });

  it("does not appear on a failure read back from the server with no code", () => {
    render(<MessageBubble convId="c1" message={failed()} showRegenerate />);
    expect(screen.queryByTestId("new-message-instead")).toBeNull();
  });

  it("moves the caret to the composer, which is the whole offer", () => {
    const ta = document.createElement("textarea");
    ta.setAttribute("data-shortcut", "composer");
    document.body.appendChild(ta);
    render(
      <MessageBubble
        convId="c1"
        message={failed({ error_code: "upstream_bad_response", error_retryable: false })}
        showRegenerate={false}
      />,
    );
    screen.getByTestId("new-message-instead").click();
    expect(document.activeElement).toBe(ta);
    ta.remove();
  });
});
