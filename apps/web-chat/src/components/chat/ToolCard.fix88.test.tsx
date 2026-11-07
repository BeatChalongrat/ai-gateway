// FIX88 Part E — a failed attempt that a later attempt of the same tool
// made good folds to its header row (collapse, never delete — FIX77's
// rule). The owner's screenshot: "Asked you a question · Failed · Invalid
// question: questions[0].header is longer than 12 characters" sat fully
// expanded, raw JSON and all, immediately above the question that worked.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { ToolActivity } from "@/lib/types";

import { ToolActivityList, supersededFailureIds } from "./ToolCard";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

function act(over: Partial<ToolActivity> & { id: string; tool: string }): ToolActivity {
  return { phase: "finished", success: true, parentId: null, ...over } as ToolActivity;
}

describe("supersededFailureIds", () => {
  it("a failure followed by a success of the same tool in the same group is superseded", () => {
    const a = act({ id: "q1", tool: "ask_user_question", success: false, resultPreview: "Invalid question" });
    const b = act({ id: "q2", tool: "ask_user_question", success: true });
    expect([...supersededFailureIds([a, b])]).toEqual(["q1"]);
  });
  it("a failure with no later success stays open; a later failure does not make an earlier one good", () => {
    const a = act({ id: "r1", tool: "run_command", success: false });
    const b = act({ id: "r2", tool: "run_command", success: false });
    expect(supersededFailureIds([a, b]).size).toBe(0);
    expect(supersededFailureIds([a]).size).toBe(0);
  });
  it("only the same tool in the same sibling group supersedes; a running retry does not yet", () => {
    const a = act({ id: "x1", tool: "read_document", success: false });
    const other = act({ id: "y1", tool: "run_command", success: true });
    const nested = act({ id: "x2", tool: "read_document", success: true, parentId: "sub-1" });
    const running = act({ id: "x3", tool: "read_document", phase: "started", success: undefined });
    expect(supersededFailureIds([a, other, nested, running]).size).toBe(0);
  });
});

describe("the list folds a superseded failure", () => {
  it("the failed ask_user_question row stays, starts folded, says it was retried, and its raw input is behind the chevron", () => {
    const failed = act({ id: "q1", tool: "ask_user_question", success: false, input: '{"questions":[{"header":"หัวข้อเรียงความยาวมาก"}]}', resultPreview: "Invalid question: questions[0].header is longer than 12 characters" });
    const worked = act({ id: "q2", tool: "ask_user_question", success: true });
    render(<ToolActivityList activities={[failed, worked]} />);
    const cards = screen.getAllByTestId("tool-card");
    expect(cards).toHaveLength(1); // the successful one is the QuestionCard's business (FIX77 ③)
    const card = cards[0]!;
    expect(card.getAttribute("data-status")).toBe("failed");
    expect(card.getAttribute("data-folded-failure")).toBe("true");
    expect(screen.getByTestId("tool-retried-note").textContent).toBe("earlier attempt · retried below");
    expect(screen.getByText("Failed")).toBeTruthy(); // never hidden
    expect(screen.queryByTestId("tool-input-detail")).toBeNull(); // folded: raw input not shown
  });
  it("a failure nothing retried keeps FIX77's behaviour: open, red, raw input shown", () => {
    const failed = act({ id: "r1", tool: "run_command", success: false, input: "rm -rf /tmp/x", resultPreview: "permission denied" });
    render(<ToolActivityList activities={[failed]} />);
    const card = screen.getByTestId("tool-card");
    expect(card.getAttribute("data-folded-failure")).toBeNull();
    expect(screen.getByTestId("tool-input-detail").textContent).toBe("rm -rf /tmp/x");
  });
});
