// AM1 — the workspace thread's merge/order rules.
import { describe, expect, it } from "vitest";

import { maxRev, mergeItems, sortItems, timeKey } from "./thread";
import type { ThreadItem } from "./types";

const item = (id: string, rev: number, created_at: string, kind = "agent_text", payload: unknown = {}): ThreadItem => ({
  id,
  install_id: "i",
  kind,
  payload,
  rev,
  created_at,
});

describe("thread merge", () => {
  it("an update (same id, higher rev) replaces the older copy", () => {
    const a1 = item("a", 1, "2026-09-26T01:00:00Z", "card", { status: "open" });
    const a5 = item("a", 5, "2026-09-26T01:00:00Z", "card", { status: "answered" });
    const merged = mergeItems([a1], [a5]);
    expect(merged).toHaveLength(1);
    expect((merged[0]!.payload as { status: string }).status).toBe("answered");
  });

  it("a stale copy (lower or equal rev) is ignored and the array is returned as is", () => {
    const a5 = item("a", 5, "2026-09-26T01:00:00Z", "card", { status: "answered" });
    const cur = [a5];
    expect(mergeItems(cur, [item("a", 3, "2026-09-26T01:00:00Z", "card", { status: "open" })])).toBe(cur);
    expect(mergeItems(cur, [item("a", 5, "2026-09-26T01:00:00Z", "card", { status: "open" })])).toBe(cur);
  });

  it("orders by created_at, then rev — an item's position does not move when it is updated", () => {
    const greet = item("g", 1, "2026-09-26T01:00:00Z", "greeting");
    const prog = item("p", 2, "2026-09-26T01:05:00Z", "progress");
    const text = item("t", 3, "2026-09-26T01:06:00Z", "agent_text");
    let list = mergeItems([], [text, prog, greet]);
    expect(list.map((i) => i.id)).toEqual(["g", "p", "t"]);
    // the progress card ticks (rev 9) but keeps its place
    list = mergeItems(list, [item("p", 9, "2026-09-26T01:05:00Z", "progress")]);
    expect(list.map((i) => i.id)).toEqual(["g", "p", "t"]);
  });

  it("same transaction (equal created_at): the person's message comes before the run it started", () => {
    // message → run's progress card (rev 11) → message linked to the run (rev 12)
    const progress = item("p", 11, "2026-09-26T01:00:00.123456Z", "progress");
    const user = item("u", 12, "2026-09-26T01:00:00.123456Z", "user_files");
    expect(sortItems([progress, user]).map((i) => i.id)).toEqual(["u", "p"]);
  });

  it("microseconds count (Date.parse alone would tie them)", () => {
    expect(timeKey("2026-09-26T01:00:00.123457Z")).toBeGreaterThan(timeKey("2026-09-26T01:00:00.123456Z"));
    const a = item("a", 9, "2026-09-26T01:00:00.123457Z");
    const b = item("b", 1, "2026-09-26T01:00:00.123456Z");
    expect(sortItems([a, b]).map((i) => i.id)).toEqual(["b", "a"]);
  });

  it("maxRev is the stream's resume point", () => {
    expect(maxRev([item("a", 4, "2026-09-26T01:00:00Z"), item("b", 9, "2026-09-26T01:00:00Z")])).toBe(9);
    expect(maxRev([], 7)).toBe(7);
  });
});
