// FIX119 — the page's copy of the stop-reason set is pinned to the contract,
// and every reason the page speaks about has an EN and a TH sentence.
import fs from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { dictionaries } from "./i18n/dict";
import { STOP_REASONS, STOP_REASON_NOTICE_KEY, isStopReason } from "./terminalCause";

const CONTRACT = resolve(__dirname, "../../../../docs/contracts/terminal_cause.json");
const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8")) as { stop_reasons: string[] };

describe("FIX119 — stop reasons on the page", () => {
  it("the set is the contract's set, in order", () => {
    expect([...STOP_REASONS]).toEqual(contract.stop_reasons);
    for (const r of contract.stop_reasons) expect(isStopReason(r)).toBe(true);
    expect(isStopReason("length")).toBe(false);
  });

  it("every reason the page tells the person about has both sentences, and the silent ones are the ones that carry no news", () => {
    for (const [reason, key] of Object.entries(STOP_REASON_NOTICE_KEY)) {
      expect(isStopReason(reason)).toBe(true);
      expect(dictionaries.en[key]).toBeTruthy();
      expect(dictionaries.th[key]).toBeTruthy();
    }
    // FIX124 — tool_use is no longer silent: on a finished row it means the
    // turn ended while the model still wanted tools (the tool-step limit).
    for (const silent of ["end_turn", "unknown"]) expect(silent in STOP_REASON_NOTICE_KEY).toBe(false);
    expect(STOP_REASON_NOTICE_KEY.tool_use).toBe("msg.stopToolLimit");
    expect(dictionaries.th["msg.stopMaxTokens"]).toContain("ยังไม่จบ");
  });

  it("the reference-id strings exist in both languages", () => {
    for (const key of ["error.referenceId", "error.copyReference", "error.referenceCopied"] as const) {
      expect(dictionaries.en[key]).toBeTruthy();
      expect(dictionaries.th[key]).toBeTruthy();
    }
  });
});
