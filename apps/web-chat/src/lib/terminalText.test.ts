// FIX107 Part A1 — which lines of a user message are a terminal table.
import { describe, expect, it } from "vitest";

import { splitTerminalBlocks } from "./terminalText";

const TABLE = [
  "┌────┬─────────┐",
  "│ id │ service │",
  "├────┼─────────┤",
  "│ 1  │ gateway │",
  "└────┴─────────┘",
].join("\n");

describe("splitTerminalBlocks", () => {
  it("a pasted psql table becomes one terminal block between the lines around it", () => {
    const segs = splitTerminalBlocks(`ช่วยดูผลนี้หน่อยครับ\nsran=# SELECT id, service FROM t;\n${TABLE}\n(1 row)`);
    expect(segs.map((s) => s.kind)).toEqual(["text", "terminal", "text"]);
    expect(segs[0]!.text).toBe("ช่วยดูผลนี้หน่อยครับ\nsran=# SELECT id, service FROM t;");
    expect(segs[1]!.text).toBe(TABLE);
    expect(segs[2]!.text).toBe("(1 row)");
  });

  it("Thai prose, however long, never takes the terminal path and comes back untouched", () => {
    const thai = "ช่วยอ่าน log ด้านล่างแล้วบอกหน่อยครับว่าบริการไหนมีปัญหา ".repeat(40);
    expect(splitTerminalBlocks(thai)).toEqual([{ kind: "text", text: thai }]);
  });

  it("one decorated line is not a table", () => {
    const one = "สรุป\n────────\nจบ";
    expect(splitTerminalBlocks(one)).toEqual([{ kind: "text", text: one }]);
  });

  it("Windows line endings from a pasted console still find the block", () => {
    const segs = splitTerminalBlocks(`before\r\n${TABLE.replace(/\n/g, "\r\n")}\r\nafter`);
    expect(segs.map((s) => s.kind)).toEqual(["text", "terminal", "text"]);
    expect(segs[1]!.text).toBe(TABLE);
  });

  it("a message that is only a table is a single terminal block", () => {
    expect(splitTerminalBlocks(TABLE)).toEqual([{ kind: "terminal", text: TABLE }]);
  });
});
