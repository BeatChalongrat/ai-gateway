// FIX31 Part 3 — the prompt's statement about the shell must match what the
// sidecar's run_command does. Before this round both prompts said "shell
// commands run in PowerShell-compatible cmd": a bare Get-Date ran in cmd.exe and
// failed, and the model burned consent prompts hunting for a wrapper. The
// sidecar now defaults to Windows PowerShell 5.1 (RunCommandTool.PickShell,
// pinned by ShellInterpreterTests on the .NET side); this pins that BOTH prompts
// say so, in the same words, and that the old claim is gone from the source.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { SHELL_ENVIRONMENT_LINE, subAgentSystemPrompt } from "../src/main/prompt-assembler";

describe("FIX31 Part 3 — the prompt tells the truth about the shell", () => {
  it("names Windows PowerShell 5.1 as the default and cmd /c as the escape hatch", () => {
    expect(SHELL_ENVIRONMENT_LINE).toMatch(/Windows PowerShell 5\.1 by default/);
    expect(SHELL_ENVIRONMENT_LINE).toMatch(/`cmd \/c`/);
    expect(SHELL_ENVIRONMENT_LINE).not.toMatch(/PowerShell-compatible cmd/);
  });

  it("the sub-agent prompt carries the same line", () => {
    const p = subAgentSystemPrompt("C:\\Users\\x", []);
    expect(p).toContain(SHELL_ENVIRONMENT_LINE);
    expect(p).not.toContain("PowerShell-compatible cmd");
  });

  it("the main prompt uses the same constant and no second statement about the shell survives", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "src", "main", "prompt-assembler.ts"), "utf8");
    expect(src.match(/SHELL_ENVIRONMENT_LINE/g)?.length ?? 0).toBeGreaterThanOrEqual(3); // the const + both prompts
    expect(src).not.toContain("PowerShell-compatible cmd.");
    expect(src.match(/shell commands run in/g) ?? []).toHaveLength(0);
  });
});
