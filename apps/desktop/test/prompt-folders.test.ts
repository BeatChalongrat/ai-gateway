// FIX26 Part 3 — the system prompt NAMES the granted folders (enforcing
// without telling was half a design: the model guessed the home directory).
import { describe, expect, it } from "vitest";

import { baseSystemPrompt, folderAccessSection, subAgentSystemPrompt } from "../src/main/prompt-assembler";

const HOME = "C:\\Users\\tester";
const GRANTS = ["C:\\Users\\tester\\Desktop\\sran-test-folder", "D:\\data"];

describe("folderAccessSection", () => {
  it("names every granted folder by absolute path and says relative paths go into the first", () => {
    const s = folderAccessSection(HOME, GRANTS);
    for (const g of GRANTS) expect(s).toContain(`  - ${g}`);
    expect(s).toContain(`resolves into the first granted folder (${GRANTS[0]})`);
  });

  it("says the home directory is NOT granted as a whole and must not be used as a workspace", () => {
    const s = folderAccessSection(HOME, GRANTS);
    expect(s).toContain(`home directory (${HOME}) is NOT granted as a whole`);
    expect(s).toContain("do not list it, do not write into it");
  });

  it("forbids suggesting system folders and points refusals at the folder chip", () => {
    const s = folderAccessSection(HOME, GRANTS);
    expect(s).toMatch(/NEVER suggest granting C:\\+Windows, Program Files, ProgramData, the whole home folder/);
    expect(s).toContain("one click on the folder chip");
  });

  it("with no grants: tells the model every path will be refused and how the user adds one", () => {
    const s = folderAccessSection(HOME, []);
    expect(s).toContain("none is granted yet");
    expect(s).toContain("folder chip");
    expect(s).not.toContain("resolves into the first");
  });
});

describe("prompts carry the section", () => {
  it("base prompt (tools on) and sub-agent prompt both name the grants; the old 'user profile' sentence is gone", () => {
    for (const p of [baseSystemPrompt(HOME, true, GRANTS), subAgentSystemPrompt(HOME, GRANTS)]) {
      expect(p).toContain(GRANTS[1]);
      expect(p).not.toContain("File tools can access paths under your user");
      expect(p).not.toContain("paths under the user profile");
      expect(p).not.toContain("relative paths resolve against the home directory");
    }
  });
});
