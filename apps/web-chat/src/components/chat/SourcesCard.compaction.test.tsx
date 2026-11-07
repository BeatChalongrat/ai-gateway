// FIX102 Part E — the compaction row is worded by the client from a code,
// in the reader's language, instead of an English sentence built in Go.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextSource } from "@/lib/types";

import { SourcesCard } from "./SourcesCard";

const row = (metadata: Record<string, unknown> | null, display_label = "Conversation compacted (22 older messages summarized)"): ContextSource => ({
  id: "r1",
  source_type: "compacted_history",
  display_label,
  metadata,
  created_at: "2026-09-10T00:00:00Z",
});

function open(sources: ContextSource[]) {
  render(<SourcesCard sources={sources} />);
  fireEvent.click(screen.getByRole("button", { expanded: false }));
}

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("SourcesCard compaction row", () => {
  it("a Thai screen reads Thai, not the Go sentence", () => {
    open([row({ code: "conversation_compacted", missing: 22, summary: "complete" })]);
    expect(screen.getByText(dictionaries.th["sources.compacted"].replace("{n}", "22"))).toBeTruthy();
    expect(screen.queryByText(/Conversation compacted/)).toBeNull();
  });

  it("each summary status has its own wording", () => {
    open([
      row({ code: "conversation_compacted", missing: 5, summary: "failed" }),
      { ...row({ code: "conversation_compacted", missing: 6, summary: "partial" }), id: "r2" },
      { ...row({ code: "conversation_compacted", missing: 7, summary: "off" }), id: "r3" },
    ]);
    expect(screen.getByText(dictionaries.th["sources.compacted.noSummary"].replace("{n}", "5"))).toBeTruthy();
    expect(screen.getByText(dictionaries.th["sources.compacted.partial"].replace("{n}", "6"))).toBeTruthy();
    expect(screen.getByText(dictionaries.th["sources.compacted.memoryOff"].replace("{n}", "7"))).toBeTruthy();
  });

  it("a row stored before the code existed keeps its stored label", () => {
    open([row(null, "Conversation compacted (3 older messages summarized)")]);
    expect(screen.getByText("Conversation compacted (3 older messages summarized)")).toBeTruthy();
  });
});
