// How long a tool took, said in the language on screen. While a tool runs the
// thread already counts in "วินาที" on a Thai screen; the finished row said
// "12 ms" in both languages — the one unit the hard-coded-string baseline was
// still carrying.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { ToolActivity } from "@/lib/types";

import { ToolActivityList } from "./ToolCard";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("a finished tool row's duration", () => {
  const done = { id: "c1", tool: "read_document", phase: "finished", success: true, parentId: null, argsSummary: "path=งบ.xlsx", subject: "งบ.xlsx", durationMs: 12 } as ToolActivity;

  it("is worded in the app's language and follows a switch", () => {
    render(<ToolActivityList activities={[done]} />);
    const row = screen.getByTestId("tool-card");
    expect(row.textContent).toContain("12 ms");
    act(() => useI18nStore.setState({ locale: "th" }));
    expect(row.textContent).toContain("12 มิลลิวินาที");
    expect(row.textContent).not.toContain("12 ms");
  });
});
