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

  it("an open chip is found; an answered one is not", () => {
    const chip = item("intent", 60, { status: "open", files: [{ id: "f3", name: "x.pdf", size: 1 }] });
    expect(openIntent([greeting, chip])?.id).toBe(chip.id);
    expect(openIntent([greeting, { ...chip, payload: { status: "chosen" } }])).toBeNull();
  });
});
