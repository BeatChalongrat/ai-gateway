// FIX33 B6 — skills as starter templates: enabled skills only, the trigger
// phrase (or the name) is what the composer receives, and they join the "/"
// list after the fixed commands without hijacking prose.
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: { listSkills: vi.fn() } }));
vi.mock("./hostApi", () => ({ hostApi: { listSkills: vi.fn() } }));

import { filterSlashCommands, SLASH_COMMANDS, shouldShowSlashMenu } from "./slashCommands";
import { skillInsertText, skillKeyword, skillSlashCommands, templatesFromHost, templatesFromWeb } from "./skillTemplates";

describe("skill templates", () => {
  it("the trigger phrase is what the composer gets; the name when there is none", () => {
    expect(skillInsertText({ name: "Weekly report", triggers: ["weekly report", "wr"] })).toBe("weekly report ");
    expect(skillInsertText({ name: "Weekly report", triggers: [] })).toBe("Weekly report: ");
    expect(skillInsertText({ name: "รายงานประจำสัปดาห์", triggers: null })).toBe("รายงานประจำสัปดาห์: ");
  });

  it("only ENABLED skills become templates (web and host shapes)", () => {
    const web = templatesFromWeb([
      { id: "1", name: "A", description: "d", triggers: ["a"], instructions: "", enabled: true, created_at: "", updated_at: "" },
      { id: "2", name: "B", description: "", triggers: [], instructions: "", enabled: false, created_at: "", updated_at: "" },
    ]);
    expect(web.map((t) => t.id)).toEqual(["1"]);
    const host = templatesFromHost([
      { id: "h1", name: "H", description: "", triggers: [], isBuiltin: false, isEnabled: true },
      { id: "h2", name: "Off", description: "", triggers: [], isBuiltin: false, isEnabled: false },
    ]);
    expect(host.map((t) => t.id)).toEqual(["h1"]);
  });

  it("keywords are slash-safe and Thai survives", () => {
    expect(skillKeyword("Weekly Report!")).toBe("weekly-report");
    expect(skillKeyword("รายงาน ประจำสัปดาห์")).toBe("รายงาน-ประจำสัปดาห์");
    expect(skillKeyword("???")).toBe("skill");
  });

  it("they join the / list after the fixed commands and filter by prefix", () => {
    const extra = skillSlashCommands([{ id: "1", name: "Weekly report", description: "Mondays", insertText: "weekly report " }]);
    const all = filterSlashCommands("", extra);
    expect(all.slice(0, SLASH_COMMANDS.length)).toEqual(SLASH_COMMANDS);
    expect(all[all.length - 1]).toMatchObject({ id: "skill", keyword: "weekly-report", label: "Weekly report", insertText: "weekly report " });
    expect(filterSlashCommands("wee", extra).map((c) => c.keyword)).toEqual(["weekly-report"]);
    expect(filterSlashCommands("img", extra).map((c) => c.id)).toEqual(["image"]);
    // The trigger rule is unchanged: "/" mid-sentence is prose.
    expect(shouldShowSlashMenu("/weekly")).toBe(true);
    expect(shouldShowSlashMenu("24/7")).toBe(false);
  });
});
