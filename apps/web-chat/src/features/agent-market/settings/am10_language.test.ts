// AM10 F — the settings page offered "ภาษาของเอกสาร: ไทย / อังกฤษ" and no
// document ever followed the choice. The row now states what the documents
// are and offers no editor.
import { describe, expect, it } from "vitest";

import { amDictionaries, tAm } from "../i18n";
import type { Default } from "../types";
import { describeValue, editorFor } from "./SettingsPage";

describe("the document language is a fact, not a choice that does nothing", () => {
  it("has no editor", () => {
    expect(editorFor("language")).toBeNull();
    expect(editorFor("currency")).toBe("currency"); // the others keep theirs
  });

  it("the row says the documents are Thai, whatever was stored", () => {
    for (const value of ["th", "en"]) {
      expect(describeValue({ key: "language", value, source: "install" } as Default, tAm)).toEqual(["ไทย — เอกสารของ agent มีภาษาไทยอย่างเดียวในตอนนี้"]);
    }
  });

  it("no word for an English edition is left in the dictionary", () => {
    expect("lang.en" in (amDictionaries.th as Record<string, string>)).toBe(false);
  });
});
