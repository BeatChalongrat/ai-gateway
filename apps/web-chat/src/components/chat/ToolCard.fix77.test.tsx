// FIX77 — what the transcript's tool rows show. Pins the three fixes:
//   ② rows carry a friendly, localised label + at most ONE useful argument
//     (subject); the raw argument dump never renders on the row and stays
//     reachable behind the expand;
//   ③ a call that a dedicated card already presents (ask_user_question →
//     QuestionCard, update_plan → PlanChecklistCard) renders no row —
//     UNLESS it failed, because a hidden tool must never fail silently;
//   ① a repeated tool's earlier image folds behind a "superseded" toggle
//     once a later run of the same tool succeeded (collapse, never
//     delete); if the final run failed, nothing folds.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import type { ToolActivity } from "@/lib/types";

import { ToolActivityList, supersededImageIds } from "./ToolCard";

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

const PNG = "data:image/png;base64,AAAA";

function act(over: Partial<ToolActivity> & { id: string; tool: string }): ToolActivity {
  return { phase: "finished", success: true, parentId: null, ...over } as ToolActivity;
}

describe("FIX77 ② — friendly labels instead of an argument dump", () => {
  it("the row shows the label and the one-field subject; the raw args only behind the expand", () => {
    const a = act({ id: "c1", tool: "read_document", argsSummary: 'path=C:\\Users\\x\\งบ.xlsx, pages=all', subject: "งบ.xlsx", durationMs: 12 });
    render(<ToolActivityList activities={[a]} />);
    expect(screen.getByTestId("tool-label").textContent).toBe("Read a document");
    expect(screen.getByTestId("tool-subject").textContent).toBe("งบ.xlsx");
    // the dump is NOT on the header row
    expect(screen.getByTestId("tool-card").querySelector("button")!.textContent).not.toContain("path=");
    // …but the call still expands to show it (the Claude Code contract)
    fireEvent.click(screen.getByTestId("tool-label"));
    expect(screen.getByTestId("tool-input-detail").textContent).toContain("path=");
  });

  it("an unmapped (MCP) tool falls back to its NAME alone — never a JSON dump", () => {
    const a = act({ id: "c2", tool: "echo_nonce", argsSummary: '{"secret":"x"}' });
    render(<ToolActivityList activities={[a]} />);
    expect(screen.getByTestId("tool-label").textContent).toBe("Used Echo nonce");
    expect(screen.getByTestId("tool-card").querySelector("button")!.textContent).not.toContain("secret");
  });

  it("speaks Thai when the locale is Thai, and marks a failure in words", () => {
    useI18nStore.setState({ locale: "th" });
    const a = act({ id: "c3", tool: "run_python", success: false, resultPreview: "Error: boom", isCodeRun: true, codeText: "x" });
    render(<ToolActivityList activities={[a]} />);
    expect(screen.getByTestId("tool-label").textContent).toBe("รันโค้ด Python");
    expect(screen.getByText("ไม่สำเร็จ")).toBeTruthy();
    // a failed card stays open with its error visible
    expect(screen.getByText("Error: boom")).toBeTruthy();
  });

  it("a thin (web started) frame renders the label + subject, no dump", () => {
    const a = act({ id: "c4", tool: "search_past_conversations", phase: "started", success: undefined, subject: "excel plan" });
    render(<ToolActivityList activities={[a]} />);
    expect(screen.getByText("Searching past chats…")).toBeTruthy();
    expect(screen.getByTestId("tool-subject").textContent).toBe("excel plan");
  });
});

describe("FIX77 ③ — rows a dedicated card already presents", () => {
  it("ask_user_question renders no row (the QuestionCard is the presentation)", () => {
    // the merged activity as the store holds it after started+finished
    const a = act({ id: "q1", tool: "ask_user_question", argsSummary: 'questions=[{"question":"ข้อไหน"…', resultPreview: "answered" });
    const { container } = render(<ToolActivityList activities={[a]} />);
    expect(container.firstChild).toBeNull();
    cleanup();
    // and while it is still running (the card is on screen instead)
    const started = act({ id: "q1", tool: "ask_user_question", phase: "started", success: undefined, argsSummary: "questions=…" });
    const r2 = render(<ToolActivityList activities={[started]} />);
    expect(r2.container.firstChild).toBeNull();
  });

  it("…but a FAILED ask_user_question still surfaces (never fail silently)", () => {
    const a = act({ id: "q2", tool: "ask_user_question", success: false, resultPreview: "Invalid question: too many options", argsSummary: "questions=…" });
    render(<ToolActivityList activities={[a]} />);
    expect(screen.getByTestId("tool-card").getAttribute("data-status")).toBe("failed");
    expect(screen.getByText("Invalid question: too many options")).toBeTruthy();
  });

  it("update_plan hides only while the plan card is shown; failures always render", () => {
    const ok = act({ id: "p1", tool: "update_plan", argsSummary: "steps=[…]" });
    const failed = act({ id: "p2", tool: "update_plan", success: false, resultPreview: "Error" });
    const { rerender } = render(<ToolActivityList activities={[ok]} planShown />);
    expect(screen.queryByTestId("tool-card")).toBeNull();
    rerender(<ToolActivityList activities={[ok]} planShown={false} />);
    expect(screen.getByTestId("tool-card")).toBeTruthy();
    cleanup();
    render(<ToolActivityList activities={[failed]} planShown />);
    expect(screen.getByTestId("tool-card").getAttribute("data-status")).toBe("failed");
  });
});

describe("FIX77 ① — a later run supersedes an earlier image (collapse, never delete)", () => {
  const first = act({ id: "r1", tool: "run_python", imageDataUri: PNG, isCodeRun: true, codeText: "plot v1" });
  const second = act({ id: "r2", tool: "run_python", imageDataUri: PNG, isCodeRun: true, codeText: "plot v2" });

  it("only the LAST image renders full-size; the earlier one folds behind a toggle and comes back on click", () => {
    render(<ToolActivityList activities={[first, second]} />);
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(1);
    const toggle = screen.getByTestId("superseded-image-toggle");
    expect(toggle.textContent).toContain("Superseded result");
    fireEvent.click(toggle);
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(2);
  });

  it("if the FINAL attempt failed, nothing collapses — every result stays open", () => {
    const failedLast = act({ id: "r3", tool: "run_python", success: false, imageDataUri: PNG, resultPreview: "Error", isCodeRun: true, codeText: "x" });
    render(<ToolActivityList activities={[first, second, failedLast]} />);
    expect(screen.queryByTestId("superseded-image-toggle")).toBeNull();
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(3);
  });

  it("different tools never supersede each other, and a still-running retry collapses nothing", () => {
    const other = act({ id: "s1", tool: "browser_screenshot", imageDataUri: PNG });
    const running = act({ id: "r4", tool: "run_python", phase: "started", success: undefined, isCodeRun: true, codeText: "y" });
    expect(supersededImageIds([first, other]).size).toBe(0);
    expect(supersededImageIds([first, second, running]).size).toBe(0);
  });

  // FIX79 — the owner's rule: alternatives offered for a choice must ALL
  // be visible; only retries (a later run of the same tool) fold.
  it("several images from ONE run are alternatives — all render, nothing folds", () => {
    const alts = act({ id: "a1", tool: "run_python", imageDataUris: [PNG, PNG, PNG], isCodeRun: true, codeText: "3 options" });
    render(<ToolActivityList activities={[alts]} />);
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(3);
    expect(screen.queryByTestId("superseded-image-toggle")).toBeNull();
  });

  it("a later run supersedes an earlier run's WHOLE set together, and the toggle restores it whole", () => {
    const earlierPair = act({ id: "a2", tool: "run_python", imageDataUris: [PNG, PNG], isCodeRun: true, codeText: "v1 pair" });
    const finalPair = act({ id: "a3", tool: "run_python", imageDataUris: [PNG, PNG], isCodeRun: true, codeText: "v2 pair" });
    render(<ToolActivityList activities={[earlierPair, finalPair]} />);
    // only the final run's two alternatives show; ONE toggle for the folded pair
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(2);
    expect(screen.getAllByTestId("superseded-image-toggle")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("superseded-image-toggle"));
    expect(screen.getAllByAltText("tool screenshot")).toHaveLength(4);
  });
});
