import { describe, expect, it } from "vitest";

import { FORMAT_KEYS, MORE_FORMATS } from "./OutputsCard";

// AM1b: PDF left the outputs (its Thai shaping was wrong); it returns in AM-2
// as the finished .docx converted by LibreOffice.
describe("output formats", () => {
  it("the 'more files' menu offers no PDF", () => {
    expect(MORE_FORMATS).not.toContain("pdf");
    expect(Object.keys(FORMAT_KEYS)).not.toContain("pdf");
  });
});
