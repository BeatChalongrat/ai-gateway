// AM10 F — the settings page offered "ภาษาของเอกสาร: ไทย / อังกฤษ" and no
// document ever followed the choice. The row then stated that the documents
// are Thai and offered no editor.
//
// AM18 — the documents follow the run's language, and the install's own
// language is the one a person asked for from now on in the chat (or set).
// The row says which it is, in the reader's language, and still has no
// editor of its own: it is asked for in words and cleared with the one
// button every remembered answer has.
import { describe, expect, it } from "vitest";

import { amDictionaries, tAm } from "../i18n";
import type { Default } from "../types";
import { describeValue, editorFor } from "./SettingsPage";

describe("the document language is what the documents are made in", () => {
  it("has no editor", () => {
    expect(editorFor("language")).toBeNull();
    expect(editorFor("currency")).toBe("currency"); // the others keep theirs
  });

  it("the row says the language stored", () => {
    expect(describeValue({ key: "language", value: "th", source: "answer" } as Default, tAm)).toEqual(["ไทย"]);
    expect(describeValue({ key: "language", value: "en", source: "answer" } as Default, tAm)).toEqual(["อังกฤษ"]);
  });

  it("both languages have a word in the dictionary", () => {
    for (const key of ["lang.th", "lang.en"]) {
      expect(key in (amDictionaries.th as Record<string, string>)).toBe(true);
      expect(key in (amDictionaries.en as Record<string, string>)).toBe(true);
    }
  });

  it("standing requests are read by the package's own labels", () => {
    const manifest = { instructions: { can: [{ id: "sort_by_date", label: "เรียงรายการกระทบยอดตามวันที่" }] } } as never;
    expect(describeValue({ key: "instructions", value: ["sort_by_date", "no_such"], source: "answer" } as Default, tAm, manifest)).toEqual([
      "เรียงรายการกระทบยอดตามวันที่",
      "no_such",
    ]);
  });
});
