// FIX130 A3b — the answer marker ("memory saved / suggested") comes from ONE
// place: the live SSE frame when the page watched the turn, the persisted
// context_sources rows after a reload. These pin the accumulation of
// repeated memory_saved frames and the reload derivation, so the chip means
// the same thing on both paths. (FIX132 A removed the "used N memories"
// marker and its helpers; MessageBubble.memoryChip.test.tsx pins its absence.)
import { describe, expect, it } from "vitest";

import type { ContextSource, Message } from "@/lib/types";

import { memorySavedFromSources, mergeMemorySaved, messageMemorySaved } from "./memoryMarkers";

const src = (over: Partial<ContextSource>): ContextSource =>
  ({ id: "s", source_type: "saved_memory", display_label: "", created_at: "2026-09-23T00:00:00Z", ...over }) as ContextSource;

describe("mergeMemorySaved — memory_saved may arrive more than once per turn", () => {
  it("accumulates by id; a later frame updates the status of the same memory", () => {
    const first = mergeMemorySaved(undefined, { items: [{ id: "m1", label: "แพ้กุ้ง", status: "pending_confirmation" }] });
    expect(first).toEqual([{ id: "m1", label: "แพ้กุ้ง", status: "pending_confirmation" }]);
    const second = mergeMemorySaved(first, {
      items: [
        { id: "m2", label: "ชื่อบีท", status: "active" },
        { id: "m1", label: "แพ้กุ้ง", status: "active" },
      ],
    });
    expect(second).toEqual([
      { id: "m1", label: "แพ้กุ้ง", status: "active" },
      { id: "m2", label: "ชื่อบีท", status: "active" },
    ]);
  });
  it("ignores items with an unknown status and returns the previous list for an empty frame", () => {
    const prev = [{ id: "m1", label: "x", status: "active" as const }];
    expect(mergeMemorySaved(prev, { items: [{ id: "m9", label: "y", status: "rejected" }] })).toBe(prev);
    expect(mergeMemorySaved(undefined, { items: [] })).toBeNull();
  });
});

describe("after a reload — the same facts from context_sources", () => {
  it("memory_saved rows carry the status in metadata", () => {
    expect(
      memorySavedFromSources([
        src({ source_type: "memory_saved", source_id: "m3", display_label: "อยู่เชียงใหม่", metadata: { status: "pending_confirmation" } }),
        src({ source_type: "memory_saved", source_id: "m4", display_label: "ชื่อบีท", metadata: { status: "active" } }),
        src({ source_type: "memory_saved", source_id: "m5", display_label: "?", metadata: {} }),
      ]),
    ).toEqual([
      { id: "m3", label: "อยู่เชียงใหม่", status: "pending_confirmation" },
      { id: "m4", label: "ชื่อบีท", status: "active" },
    ]);
    expect(memorySavedFromSources([])).toBeNull();
  });
});

describe("messageMemorySaved — live wins, else the row", () => {
  const base = { id: "a1", role: "assistant", content: "", created_at: "2026-09-23T00:00:00Z" } as Message;
  it("prefers the live field", () => {
    const m = {
      ...base,
      memory_saved: [{ id: "live", label: "L", status: "active" }],
      context_sources: [src({ source_type: "memory_saved", source_id: "row", display_label: "R", metadata: { status: "active" } })],
    } as Message;
    expect(messageMemorySaved(m)?.[0].id).toBe("live");
  });
  it("derives from context_sources when the page did not watch the turn; saved_memory rows are not a save", () => {
    const m = {
      ...base,
      context_sources: [
        src({ source_id: "row", display_label: "R" }),
        src({ source_type: "memory_saved", source_id: "s1", display_label: "S", metadata: { status: "active" } }),
      ],
    } as Message;
    expect(messageMemorySaved(m)).toEqual([{ id: "s1", label: "S", status: "active" }]);
  });
  it("nothing → null", () => {
    expect(messageMemorySaved(base)).toBeNull();
  });
});
