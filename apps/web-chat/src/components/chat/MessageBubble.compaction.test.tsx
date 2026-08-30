// FIX32 §1.2 — the compaction marker is user-visible information, not
// silence (property 3 of the brief's list). The desktop host emits
// compaction_notice; the page shows this quiet line, the way web-chat's own
// compacted_history source marker reads.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

import { CompactionNotice } from "./MessageBubble";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("CompactionNotice", () => {
  it("says how many older messages are no longer sent", () => {
    render(<CompactionNotice dropped={210} />);
    const el = screen.getByTestId("compaction-notice");
    expect(el.textContent).toContain("210 older messages are no longer sent");
    expect(el.textContent).toContain("first exchange");
    expect(el.getAttribute("role")).toBe("note");
  });

  it("has a Thai wording too", () => {
    useI18nStore.setState({ locale: "th" });
    render(<CompactionNotice dropped={7} />);
    expect(screen.getByTestId("compaction-notice").textContent).toContain("7");
    expect(dictionaries.th["thread.compacted"]).toContain("บทสนทนายาว");
  });
});
