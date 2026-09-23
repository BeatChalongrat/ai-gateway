// FIX130 A3c — the Memory page says WHAT is saved, WHEN (an absolute date the
// user can read, not only "3 days ago") and FROM WHICH CHAT. The pure parts
// are pinned here: the Thai Buddhist-era date, the category labels, and the
// origin rule (chat title / deleted chat / added by hand).
import { describe, expect, it } from "vitest";

import { dictionaries } from "@/lib/i18n/dict";
import type { Memory } from "@/lib/types";

import { formatMemoryDate, memoryCategoryLabel, memoryOriginOf } from "./memoryLabels";

const mem = (over: Partial<Memory>): Memory =>
  ({
    id: "m1",
    scope_type: "global_user",
    memory_type: "explicit_fact",
    content: "ชื่อเล่นว่าบีท",
    source_type: "assistant_inferred",
    status: "active",
    confidence_score: 0.9,
    importance_score: 0.5,
    is_user_confirmed: true,
    is_pinned: false,
    sensitivity_level: "normal",
    use_count: 0,
    created_at: "2026-09-23T05:00:00Z",
    updated_at: "2026-09-23T05:00:00Z",
    ...over,
  }) as Memory;

describe("formatMemoryDate", () => {
  it("writes a Thai short date in the Buddhist era", () => {
    expect(formatMemoryDate("2026-09-23T05:00:00Z", "th")).toBe("23 ก.ย. 2569");
    expect(formatMemoryDate("2027-01-02T05:00:00Z", "th")).toBe("2 ม.ค. 2570");
    expect(formatMemoryDate("2026-12-31T05:00:00Z", "th")).toBe("31 ธ.ค. 2569");
  });
  it("writes the same date in English with the Gregorian year", () => {
    expect(formatMemoryDate("2026-09-23T05:00:00Z", "en")).toBe("23 Sep 2026");
  });
  it("is empty for an unreadable timestamp", () => {
    expect(formatMemoryDate("not a date", "th")).toBe("");
  });
});

describe("memoryCategoryLabel", () => {
  const th = (k: keyof typeof dictionaries.th) => dictionaries.th[k];
  it("names the known categories in Thai", () => {
    expect(memoryCategoryLabel("identity", th)).toBe(dictionaries.th["memory.category.identity"]);
    expect(memoryCategoryLabel("response_style", th)).toBe(dictionaries.th["memory.category.response_style"]);
    expect(memoryCategoryLabel("Health", th)).toBe(dictionaries.th["memory.category.health"]);
    expect(dictionaries.th["memory.category.identity"]).toMatch(/[ก-๙]/);
  });
  it("shows an unknown category as written and nothing for none", () => {
    expect(memoryCategoryLabel("astronomy", th)).toBe("astronomy");
    expect(memoryCategoryLabel("", th)).toBeNull();
    expect(memoryCategoryLabel(undefined, th)).toBeNull();
  });
});

describe("memoryOriginOf — from which chat", () => {
  it("a chat that still exists: its title, from the server", () => {
    expect(memoryOriginOf(mem({ source_conversation_id: "c1", source_conversation_title: "แผนเที่ยวเชียงใหม่" }))).toEqual({
      kind: "chat",
      conversationId: "c1",
      title: "แผนเที่ยวเชียงใหม่",
    });
  });
  it("a deleted chat says so and links nowhere", () => {
    expect(memoryOriginOf(mem({ source_conversation_id: "c1", source_conversation_deleted: true }))).toEqual({ kind: "deleted" });
  });
  it("an older server without the title falls back to the sidebar's copy, then to an untitled link", () => {
    expect(memoryOriginOf(mem({ source_conversation_id: "c1" }), (id) => (id === "c1" ? "จากแถบข้าง" : undefined))).toEqual({
      kind: "chat",
      conversationId: "c1",
      title: "จากแถบข้าง",
    });
    expect(memoryOriginOf(mem({ source_conversation_id: "c2" }))).toEqual({ kind: "chat", conversationId: "c2", title: null });
  });
  it("added by hand on the Memory page", () => {
    expect(memoryOriginOf(mem({ source_type: "user_explicit", source_conversation_id: null }))).toEqual({ kind: "manual" });
  });
  it("anything else has no chat to name", () => {
    expect(memoryOriginOf(mem({ source_type: "artifact_event" }))).toEqual({ kind: "none" });
  });
});
