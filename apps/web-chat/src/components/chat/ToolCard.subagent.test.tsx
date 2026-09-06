// FIX29 §2.2 — one collapsible card per sub-agent, steps nested under it.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolActivity } from "@/lib/types";
import { ToolActivityList } from "./ToolCard";

const acts: ToolActivity[] = [
  { id: "call_1", tool: "run_subagent", phase: "finished", success: true, argsSummary: "2 tasks", parentId: null, durationMs: 9000 },
  { id: "subagent-a", tool: "subagent", phase: "finished", success: true, argsSummary: "count files in Desktop", parentId: "call_1", durationMs: 4000, resultPreview: "12 files" },
  { id: "c_a1", tool: "run_command", phase: "finished", success: true, argsSummary: "dir", parentId: "subagent-a", durationMs: 100 },
  { id: "c_a2", tool: "run_command", phase: "finished", success: true, argsSummary: "dir /b", parentId: "subagent-a", durationMs: 100 },
  { id: "subagent-b", tool: "subagent", phase: "finished", success: false, argsSummary: "ping google", parentId: "call_1", durationMs: 4000, resultPreview: "Stopped: error" },
  { id: "c_b1", tool: "run_command", phase: "finished", success: false, argsSummary: "ping", parentId: "subagent-b", durationMs: 100, resultPreview: "timeout" },
];

describe("sub-agent cards", () => {
  it("groups steps under one card per sub-agent; a failed sub-agent opens itself, a successful one stays collapsed", () => {
    render(<ToolActivityList activities={acts} />);
    const cards = screen.getAllByTestId("subagent-card");
    expect(cards).toHaveLength(2);
    // collapsed successful sub-agent: summary visible, its steps not rendered
    expect(screen.getByText("count files in Desktop")).toBeTruthy();
    expect(screen.queryByText("dir /b")).toBeNull();
    // failed sub-agent auto-opened: its failing step is visible with the
    // FIX77 friendly label; the failed card auto-expands so its raw input
    // ("ping") stays reachable in the detail.
    expect(screen.getAllByText("Ran a command").length).toBe(1);
    expect(screen.getByText("ping")).toBeTruthy();
    // FIX30 §3.1: it READS as failed — the word on the row, the name, the status.
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Sub-agent: ping google — Failed/ })).toBeTruthy();
    expect(cards.map((c) => c.getAttribute("data-status")).sort()).toEqual(["failed", "ok"]);
    // expand the first — its two command steps appear as labelled cards
    fireEvent.click(screen.getByRole("button", { name: /Sub-agent: count files/ }));
    expect(screen.getAllByText("Ran a command").length).toBe(3);
    expect(screen.getByText("12 files")).toBeTruthy();
  });
});
