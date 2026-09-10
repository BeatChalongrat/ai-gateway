// FIX32 §1.2 — the compaction marker is user-visible information, not
// silence (property 3 of the brief's list). The desktop host emits
// compaction_notice; the page shows this quiet line, the way web-chat's own
// compacted_history source marker reads.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { lossNoticeFromSources } from "@/lib/compaction";
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

  // FIX102 Part C — the web says so only when nothing (or not everything)
  // stands in for the messages left out.
  it("says plainly when the older messages left with no summary in their place", () => {
    render(<CompactionNotice dropped={18} summary="failed" />);
    const el = screen.getByTestId("compaction-notice");
    expect(el.textContent).toBe("📦" + dictionaries.en["thread.compacted.noSummary"].replace("{n}", "18"));
    expect(el.getAttribute("data-summary")).toBe("failed");
  });

  it("says a partial summary is partial, in Thai too", () => {
    useI18nStore.setState({ locale: "th" });
    render(<CompactionNotice dropped={9} summary="partial" />);
    expect(screen.getByTestId("compaction-notice").textContent).toContain(dictionaries.th["thread.compacted.partialSummary"].replace("{n}", "9"));
  });
});

describe("lossNoticeFromSources — the same fact after a reload", () => {
  const src = (summary: string, missing: number) => ({
    id: "s1", source_type: "compacted_history" as const, display_label: "Conversation compacted", created_at: "2026-09-10T00:00:00Z", metadata: { code: "conversation_compacted", missing, summary },
  });
  it("reads a failed or partial summary from the persisted source", () => {
    expect(lossNoticeFromSources([src("failed", 21)])).toEqual({ dropped: 21, summary: "failed" });
    expect(lossNoticeFromSources([src("partial", 4)])).toEqual({ dropped: 4, summary: "partial" });
  });
  it("is silent for a summarized trim, memory off, older rows with no metadata, and nothing", () => {
    expect(lossNoticeFromSources([src("complete", 21)])).toBeNull();
    expect(lossNoticeFromSources([src("off", 21)])).toBeNull();
    expect(lossNoticeFromSources([{ id: "s2", source_type: "compacted_history", display_label: "Conversation compacted (3 older messages summarized)", created_at: "2026-09-10T00:00:00Z" }])).toBeNull();
    expect(lossNoticeFromSources(undefined)).toBeNull();
  });
});
