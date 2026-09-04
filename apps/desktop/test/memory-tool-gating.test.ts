// FIX56.1 — the split of the memory tools into a write set and a read set.
//
// FIX56 withheld both from a memory-off chat, which was right while such a
// chat read nothing. Now it reads, so only the write half is unconditional.
// The risk in a split like this is that a tool falls out of BOTH sets and is
// silently offered again in a chat that promised nothing would be saved —
// which is why the union is pinned, not just the halves.

import { describe, expect, it } from "vitest";

import { MEMORY_READ_TOOLS, MEMORY_WRITE_TOOLS } from "../src/shared/bridge-protocol";

describe("memory tool gating sets", () => {
  it("keeps `remember` on the write side, where the gate is unconditional", () => {
    expect(MEMORY_WRITE_TOOLS.has("remember")).toBe(true);
    expect(MEMORY_READ_TOOLS.has("remember")).toBe(false);
  });

  it("still covers every tool the switch used to gate — nothing fell through the split", () => {
    // The set FIX56 gated as one. If a name leaves both halves it is
    // offered in a temporary chat again, with no test to notice.
    const gatedBefore = ["remember", "search_past_conversations"];
    const union = new Set([...MEMORY_WRITE_TOOLS, ...MEMORY_READ_TOOLS]);
    for (const name of gatedBefore) {
      expect(union.has(name), `${name} must still be gated by one half or the other`).toBe(true);
    }
  });

  it("keeps the halves disjoint — a tool cannot be both", () => {
    for (const name of MEMORY_WRITE_TOOLS) {
      expect(MEMORY_READ_TOOLS.has(name), `${name} is in both sets`).toBe(false);
    }
  });
});
