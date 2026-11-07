// FIX132 N9 — while the session check waits, the page was a lone 28-px
// spinner on white for up to 8 s (NIGHT3 f1-blank-3s). It now shows the
// brand and, after a moment, says what it is doing — in a live region, so
// it is announced too.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { SessionChecking } from "./SessionUnreachable";

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 N9 — the wait before the chat opens says something", () => {
  it("the SRAN mark at once; 'กำลังเชื่อมต่อ SRAN Chat…' after 1.5 s, announced", () => {
    render(<SessionChecking />);
    expect(screen.getByTestId("session-checking").querySelector('img[src="/sran-logo.png"]')).not.toBeNull();
    const status = screen.getByRole("status");
    expect(status).toBeEmptyDOMElement();
    act(() => void vi.advanceTimersByTime(1600));
    expect(status).toHaveTextContent(dictionaries.th["session.checking"]);
  });
});
