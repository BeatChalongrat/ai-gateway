// FIX47 Part A — the error the person reads must be true for the code
// the gateway sent.
//
// Two tables describe the same codes: backend/app/core/error_catalog.py
// (the wire default + the retryable verdict) and errorMessages.ts (the
// dictionary key per code). This test reads the backend file so the two
// cannot drift: every backend code has an EN and a TH sentence here,
// and no sentence for a non-retryable code tells the person to try
// again — that sentence, shown for a model that can never use tools,
// is the bug the round fixed.

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import {
  RETRYABLE_CODES,
  errorDictKey,
  isRetryable,
  knownErrorCodes,
  localizeErrorCode,
} from "@/lib/errorMessages";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";

const CATALOG = resolve(__dirname, "../../../../backend/app/core/error_catalog.py");

function backendCatalog(): { code: string; retryable: boolean }[] {
  const src = readFileSync(CATALOG, "utf8");
  const rows: { code: string; retryable: boolean }[] = [];
  const re = /ErrorInfo\("([a-z_]+)",\s*"[^"]*",\s*(True|False)\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    rows.push({ code: m[1], retryable: m[2] === "True" });
  }
  return rows;
}

const RETRY_WORDS = {
  en: /try again|retry/i,
  th: /ลองใหม่|ลองอีกครั้ง/,
};

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
});

describe("gateway error codes → messages", () => {
  const hasCatalog = existsSync(CATALOG);

  it.skipIf(!hasCatalog)("every backend code has an EN and a TH sentence", () => {
    const rows = backendCatalog();
    expect(rows.length).toBeGreaterThan(20);
    for (const { code } of rows) {
      const key = errorDictKey(code);
      expect(key, `no dictionary key for gateway code "${code}"`).toBeDefined();
      expect(dictionaries.en[key!]).toBeTruthy();
      expect(dictionaries.th[key!]).toBeTruthy();
    }
  });

  it.skipIf(!hasCatalog)("the retryable verdict is the same on both sides", () => {
    for (const { code, retryable } of backendCatalog()) {
      expect(isRetryable(code), `retryable(${code})`).toBe(retryable);
    }
  });

  it("no non-retryable code is told to try again, in either language", () => {
    for (const code of knownErrorCodes()) {
      if (RETRYABLE_CODES.has(code)) continue;
      const key = errorDictKey(code)!;
      expect(dictionaries.en[key], `EN ${code}`).not.toMatch(RETRY_WORDS.en);
      expect(dictionaries.th[key], `TH ${code}`).not.toMatch(RETRY_WORDS.th);
    }
  });

  it("the codes behind the owner's screenshot say what to change", () => {
    useI18nStore.setState({ locale: "th" });
    expect(localizeErrorCode("model_no_tool_support")).toContain("เครื่องมือ");
    expect(localizeErrorCode("no_capable_model")).toContain("โมเดล");
    expect(localizeErrorCode("context_length_exceeded")).toContain("ยาวเกิน");
    useI18nStore.setState({ locale: "en" });
    expect(localizeErrorCode("model_no_tool_support")).toMatch(/can't use tools/);
    expect(localizeErrorCode("model_no_tool_support")).not.toMatch(RETRY_WORDS.en);
  });

  // FIX94 Item 2 — the red box in the owner's screenshot read only "The
  // request could not be completed.": no reason, no next step. Two different
  // codes shared that one sentence (gateway_error, and the unknown-code
  // fallback), so it could not even be told which had happened. Both now name
  // a next step, and neither may imply that resending will help — the
  // non-retryable rule above still governs the wording.
  it("the dead-end codes offer a next step without promising a retry", () => {
    for (const key of ["error.gateway_error", "error.unknownNoRetry"] as const) {
      for (const lang of ["en", "th"] as const) {
        const s = dictionaries[lang][key];
        expect(s, `${lang} ${key} should name the model as a way out`).toMatch(
          lang === "en" ? /different model/i : /โมเดล/,
        );
        expect(s, `${lang} ${key} should name who to tell`).toMatch(
          lang === "en" ? /administrator/i : /ผู้ดูแล/,
        );
      }
    }
    // and they are no longer the same sentence, in either language
    expect(dictionaries.en["error.gateway_error"]).not.toBe(dictionaries.en["error.unknownNoRetry"]);
    expect(dictionaries.th["error.gateway_error"]).not.toBe(dictionaries.th["error.unknownNoRetry"]);
  });

  // The code a user actually gets when a self-hosted box has no fallback
  // chain (FIX93 measured it on 9 of 20 qwen runs) is provider_error — the
  // gateway's internal "No fallback chain registered for model 'x'" sentence
  // never reaches the screen, because this code is in the table and the
  // dictionary wins. What it lacked was the one move that works when a single
  // provider is down: change model.
  it("provider_error names the model as a way out", () => {
    expect(dictionaries.en["error.provider_error"]).toMatch(/another model/i);
    expect(dictionaries.th["error.provider_error"]).toMatch(/โมเดลอื่น/);
  });

  it("an unknown code is not told to retry unless the gateway says so", () => {
    expect(localizeErrorCode("some_new_code")).toBe(dictionaries.en["error.unknownNoRetry"]);
    expect(localizeErrorCode("some_new_code", undefined, true)).toBe(dictionaries.en["error.generic"]);
    expect(localizeErrorCode("some_new_code", "A real sentence from the gateway.")).toBe(
      "A real sentence from the gateway.",
    );
  });

  it("raw transport text never reaches the person", () => {
    const raw = 'gateway unavailable: status=400 body={"error":{"code":"x"}}';
    expect(localizeErrorCode("send_failed", raw)).toBe(dictionaries.en["error.send_failed"]);
    expect(localizeErrorCode(undefined, raw)).toBe(dictionaries.en["error.unknownNoRetry"]);
  });

  it("the gateway's retryable flag wins over the local table", () => {
    expect(isRetryable("provider_error", false)).toBe(false);
    expect(isRetryable("validation_error", true)).toBe(true);
    expect(isRetryable("validation_error")).toBe(false);
  });
});
