// AM4 — rounds in the thread: a finished round is closed by a divider (the
// latest one with "เริ่มรอบใหม่"), older rounds and finished self-tests fold.
import { describe, expect, it } from "vitest";

import type { ThreadItem } from "../types";
import { hasOwnRound, latestRound, openIntent, segments } from "./rounds";

let rev = 0;
const at = (sec: number) => new Date(Date.UTC(2026, 8, 28, 3, 0, sec)).toISOString();
function item(kind: string, sec: number, payload: Record<string, unknown>, run_id?: string): ThreadItem {
  rev += 1;
  return { id: `${kind}-${rev}`, install_id: "i-1", kind, payload, run_id, rev, created_at: at(sec) };
}

const greeting = item("greeting", 0, { text: "สวัสดีครับ" });
const selfTest = item("progress", 1, { run_id: "st", kind: "self_test", status: "succeeded" }, "st");
const selfOut = item("outputs", 2, { run_id: "st", artifacts: [{ id: "a0", name: "sample_x.xlsx", format: "xlsx", size: 1, version: 1 }] }, "st");
const sepMsg = item("user_files", 10, { files: [{ id: "f1", name: "GL_09.xlsx", size: 1 }] }, "sep");
const sep = item("progress", 10, { run_id: "sep", kind: "run", status: "succeeded" }, "sep");
const sepOut = item("outputs", 20, { run_id: "sep", period_label: "กันยายน 2569", artifacts: [{ id: "a1", name: "report_v1.docx", format: "docx", size: 1, version: 1 }] }, "sep");
const octMsg = item("user_files", 30, { files: [{ id: "f2", name: "GL_10.xlsx", size: 1 }] }, "oct");
const oct = item("progress", 30, { run_id: "oct", kind: "run", status: "succeeded" }, "oct");
const octOut = item("outputs", 40, { run_id: "oct", period_label: "ตุลาคม 2569", artifacts: [{ id: "a2", name: "report_v1.docx", format: "docx", size: 1, version: 1 }] }, "oct");

describe("rounds in the workspace", () => {
  it("a finished round is closed by its divider, carrying its files and period", () => {
    const segs = segments([greeting, sepMsg, sep, sepOut]);
    const div = segs.find((s) => s.type === "divider");
    expect(div && div.type === "divider" && div.latest && !div.started).toBe(true);
    if (div?.type === "divider") {
      expect(div.round.period).toBe("กันยายน 2569");
      expect(div.round.artifacts.map((a) => a.name)).toEqual(["report_v1.docx"]);
    }
    // the divider sits right after the files card
    const idx = segs.findIndex((s) => s.type === "divider");
    expect(segs[idx - 1]).toEqual({ type: "item", item: sepOut });
  });

  it("an older round folds; the latest stays open with the next round one click away", () => {
    const segs = segments([greeting, sepMsg, sep, sepOut, octMsg, oct, octOut]);
    const folded = segs.filter((s) => s.type === "folded");
    expect(folded).toHaveLength(1);
    if (folded[0]?.type === "folded") {
      expect(folded[0].round.runId).toBe("sep");
      expect(folded[0].items.map((i) => i.id)).toEqual([sepMsg.id, sep.id, sepOut.id]);
    }
    const dividers = segs.filter((s) => s.type === "divider");
    expect(dividers).toHaveLength(1);
    expect(dividers[0]?.type === "divider" && dividers[0].round.runId).toBe("oct");
  });

  it("a finished self-test folds at once — the workspace is still empty until a round of the person's own", () => {
    const items = [greeting, selfTest, selfOut];
    expect(segments(items).some((s) => s.type === "folded" && s.round.kind === "self_test")).toBe(true);
    expect(hasOwnRound(items)).toBe(false);
    expect(latestRound(items)).toBeNull();
    expect(hasOwnRound([...items, sepMsg, sep])).toBe(true);
  });

  it("after เริ่มรอบใหม่ the divider no longer offers it", () => {
    const pressed = item("notice", 50, { text: "เริ่มรอบใหม่แล้ว", round_start: true });
    const segs = segments([greeting, sepMsg, sep, sepOut, pressed]);
    const div = segs.find((s) => s.type === "divider");
    expect(div?.type === "divider" && div.started).toBe(true);
  });

  it("the divider closes the round after its own last words — never above them", () => {
    // the runner posts "เสร็จแล้วครับ …" after the files card, and answers to
    // questions about the round carry its run id too (the AM4 screen tour saw
    // the closing line below the divider, as if it began the next round)
    const done = item("agent_text", 21, { text: "เสร็จแล้วครับ", run_id: "sep" }, "sep");
    const ask = item("user_text", 22, { text: "ทำไมรายได้เป็นเท่านี้" }, "reply-job");
    const answer = item("agent_text", 23, { text: "รายได้ = …", run_id: "sep" }, "sep");
    const segs = segments([greeting, sepMsg, sep, sepOut, done, ask, answer]);
    const idx = segs.findIndex((s) => s.type === "divider");
    expect(segs[idx - 1]).toEqual({ type: "item", item: answer });
    expect(idx).toBe(segs.length - 1);
    // a round that ended without files: after its progress card (and its words)
    const failed = item("progress", 30, { run_id: "f", kind: "run", status: "failed" }, "f");
    const why = item("agent_text", 31, { text: "ไม่สำเร็จเพราะ…", run_id: "f" }, "f");
    const segs2 = segments([greeting, failed, why]);
    expect(segs2[segs2.length - 1]?.type).toBe("divider");
    expect(segs2[segs2.length - 2]).toEqual({ type: "item", item: why });
    // after เริ่มรอบใหม่ the round is closed: a later answer about it stays below the notice
    const pressed = item("notice", 24, { text: "เริ่มรอบใหม่แล้ว", round_start: true });
    const late = item("agent_text", 25, { text: "รายได้ = …", run_id: "sep" }, "sep");
    const segs3 = segments([greeting, sepMsg, sep, sepOut, done, pressed, late]);
    const d3 = segs3.findIndex((s) => s.type === "divider");
    expect(segs3[d3 - 1]).toEqual({ type: "item", item: done });
    expect(segs3[d3]?.type === "divider" && segs3[d3].started).toBe(true);
  });

  it("while the chip asks, the divider does not offer the same button again", () => {
    const chip = item("intent", 60, { status: "open", text: "ทำเดือนตุลาคม" });
    const div = segments([greeting, sepMsg, sep, sepOut, chip]).find((s) => s.type === "divider");
    expect(div?.type === "divider" && div.started).toBe(true);
    const answered = { ...chip, payload: { status: "chosen", choice: "about_last" } };
    const div2 = segments([greeting, sepMsg, sep, sepOut, answered]).find((s) => s.type === "divider");
    expect(div2?.type === "divider" && div2.started).toBe(false);
  });

  it("files sent before the self-test's card appeared: the self-test never swallows the person's round", () => {
    // the AM4 walkthrough on the test stack: "ใช้ค่าแนะนำทั้งหมด", files dropped at once; the self-test's
    // progress card came after the person's files, and by position the finished (folded) self-test took
    // the person's question card, files and closing words with it — the workspace showed none of them
    const msg = item("user_files", 100, { files: [{ id: "f9", name: "ใบกำกับ.pdf", size: 1 }] }, "mine");
    const mine = item("progress", 100, { run_id: "mine", kind: "run", status: "succeeded" }, "mine");
    const hello = item("greeting", 107, { text: "สวัสดีครับ" });
    const st = item("progress", 107, { run_id: "st2", kind: "self_test", status: "succeeded" }, "st2");
    const ask = item("card", 164, { questions: [] }, "mine");
    const stOut = item("outputs", 187, { run_id: "st2", artifacts: [{ id: "s", name: "sample.xlsx", format: "xlsx", size: 1, version: 1 }] }, "st2");
    const stDone = item("agent_text", 187, { text: "ทดสอบเสร็จ", run_id: "st2" }, "st2");
    const out = item("outputs", 194, { run_id: "mine", period_label: "กันยายน 2569", artifacts: [{ id: "m", name: "register.xlsx", format: "xlsx", size: 1, version: 1 }] }, "mine");
    const done = item("agent_text", 194, { text: "เสร็จแล้วครับ", run_id: "mine" }, "mine");
    const segs = segments([msg, mine, hello, st, ask, stOut, stDone, out, done]);
    const shown = segs.filter((x) => x.type === "item").map((x) => (x.type === "item" ? x.item.id : ""));
    expect(shown).toEqual([msg.id, mine.id, hello.id, ask.id, out.id, done.id]);
    const folded = segs.find((x) => x.type === "folded");
    expect(folded?.type === "folded" && folded.items.map((i) => i.id)).toEqual([st.id, stOut.id, stDone.id]);
    const div = segs.find((x) => x.type === "divider");
    expect(div?.type === "divider" && div.round.runId).toBe("mine");
    expect(segs[segs.length - 1]?.type).toBe("divider");
  });

  it("an open chip is found; an answered one is not", () => {
    const chip = item("intent", 60, { status: "open", files: [{ id: "f3", name: "x.pdf", size: 1 }] });
    expect(openIntent([greeting, chip])?.id).toBe(chip.id);
    expect(openIntent([greeting, { ...chip, payload: { status: "chosen" } }])).toBeNull();
  });
});

// AM12 — an agent on the web that may not search yet. A request typed while the install's sample run is
// still going starts nothing, and the thread says why. Neither is the sample's: when the sample folds,
// the person still sees their own words and the answer to them.
describe("a request that was refused at the agent's door", () => {
  const sample = item("progress", 1, { run_id: "st2", kind: "self_test", status: "succeeded" }, "st2");
  const asked = item("user_text", 3, { text: "ขอซื้อเก้าอี้ 20 ตัว", status: "handled", handled_as: "web_closed" });
  const why = item("notice", 3, { text: "agent นี้ยังค้นหาไม่ได้ครับ", tone: "warning", reason: "agent_closed" });
  const sampleOut = item("outputs", 9, { run_id: "st2", artifacts: [{ id: "a9", name: "sample.xlsx", format: "xlsx", size: 1, version: 1 }] }, "st2");

  it("is not folded away with the sample run it was typed during", () => {
    const segs = segments([greeting, sample, asked, why, sampleOut]);
    const folded = segs.filter((s) => s.type === "folded");
    expect(folded).toHaveLength(1);
    if (folded[0]?.type === "folded") expect(folded[0].items.map((i) => i.id)).toEqual([sample.id, sampleOut.id]);
    const shown = segs.filter((s) => s.type === "item").map((s) => (s.type === "item" ? s.item.id : ""));
    expect(shown).toEqual([greeting.id, asked.id, why.id]);
  });

  it("every other notice and typed line keeps the place it had", () => {
    const question = item("user_text", 3, { text: "ไฟล์ตัวอย่างนี้คืออะไร", status: "handled", handled_as: "reply" });
    const note = item("notice", 4, { text: "บันทึกการตั้งค่าแล้ว", tone: "success" });
    const segs = segments([greeting, sample, question, note, sampleOut]);
    const folded = segs.find((s) => s.type === "folded");
    expect(folded?.type === "folded" && folded.items.map((i) => i.id)).toEqual([sample.id, question.id, note.id, sampleOut.id]);
  });

  it("after a run of the person's own it stays in sight too, after that run's divider", () => {
    const segs = segments([greeting, sepMsg, sep, sepOut, asked, why]);
    expect(segs.filter((s) => s.type === "folded")).toHaveLength(0);
    const div = segs.findIndex((s) => s.type === "divider");
    expect(segs.slice(div + 1).map((s) => (s.type === "item" ? s.item.id : s.type))).toEqual([asked.id, why.id]);
  });
});
