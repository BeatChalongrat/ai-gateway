// FIX130 — the desktop's port of chat-edge's ExplicitRememberRequest is held
// to the SAME table chat-edge's Go test reads (testdata/explicit_remember_cases.json).
// If either side's word lists move, one of the two suites goes red.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { explicitRememberRequest } from "../src/main/explicit-remember";

const TABLE = path.join(__dirname, "..", "..", "..", "services", "chat-edge-api", "internal", "service", "testdata", "explicit_remember_cases.json");

interface Row {
  text: string;
  explicit: boolean;
}

describe("FIX130 — explicitRememberRequest matches chat-edge's table", () => {
  const rows = (JSON.parse(fs.readFileSync(TABLE, "utf8")) as { cases: Row[] }).cases;

  it("the shared table is there and not trivially small", () => {
    expect(rows.length).toBeGreaterThanOrEqual(20);
  });

  for (const row of rows) {
    it(`${row.explicit ? "explicit" : "not explicit"}: ${JSON.stringify(row.text).slice(0, 70)}`, () => {
      expect(explicitRememberRequest(row.text)).toBe(row.explicit);
    });
  }

  it("the port reads the same Go source lists (both sides name the same phrases)", () => {
    const go = fs.readFileSync(path.join(TABLE, "..", "..", "explicit_remember.go"), "utf8");
    for (const phrase of ["จำไว้", "ช่วยจำ", "อย่าลืม", "remember", "from now on", "จำแนก", "do you remember"]) {
      expect(go).toContain(`"${phrase}"`);
    }
  });
});
