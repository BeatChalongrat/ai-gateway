// FIX118 — the page's half of one closed set of terminal causes, pinned to
// docs/contracts/terminal_cause.json (chat-edge and the desktop host pin
// themselves to the same file), and the one code the page mints itself
// pinned to the catalogue and to the mapper's own table.
import fs from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { errorDictKey, isRetryable, localizeErrorCode } from "./errorMessages";
import { EOF_WITHOUT_TERMINAL_CAUSE, EOF_WITHOUT_TERMINAL_CODE, TERMINAL_CAUSES, isTerminalCause } from "./terminalCause";
import { dictionaries } from "./i18n/dict";

const CONTRACT = resolve(__dirname, "../../../../docs/contracts/terminal_cause.json");
const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8")) as { causes: string[]; transport_codes: Record<string, { code: string; retryable: boolean }> };

describe("FIX118 — terminal causes", () => {
  it("the page's set is the contract's set, in order", () => {
    expect([...TERMINAL_CAUSES]).toEqual(contract.causes);
    for (const c of contract.causes) expect(isTerminalCause(c)).toBe(true);
    expect(isTerminalCause("stream_ended_early")).toBe(false);
  });

  it("the EOF ending uses the code every surface mints for it, and the mapper knows it", () => {
    expect(EOF_WITHOUT_TERMINAL_CODE).toBe(contract.transport_codes.eof_without_terminal.code);
    expect(EOF_WITHOUT_TERMINAL_CAUSE).toBe("eof_without_terminal");
    expect(errorDictKey(EOF_WITHOUT_TERMINAL_CODE)).toBeDefined();
    expect(isRetryable(EOF_WITHOUT_TERMINAL_CODE)).toBe(contract.transport_codes.eof_without_terminal.retryable);
    // The page's old private code is unknown to the mapper — that was the bug.
    expect(errorDictKey("stream_ended_early")).toBeUndefined();
  });

  it("every code a client mints for a transport failure has an EN and a TH sentence", () => {
    for (const { code } of Object.values(contract.transport_codes)) {
      const key = errorDictKey(code);
      expect(key, code).toBeDefined();
      expect(dictionaries.en[key!]).toBeTruthy();
      expect(dictionaries.th[key!]).toBeTruthy();
    }
  });

  it("localises the EOF ending to the catalogue sentence, never a network line", () => {
    expect(localizeErrorCode(EOF_WITHOUT_TERMINAL_CODE, undefined, true)).toBe(dictionaries.en["error.stream_interrupted"]);
  });
});
