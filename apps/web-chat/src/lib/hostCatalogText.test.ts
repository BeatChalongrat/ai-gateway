// The desktop host's own catalogue, in the reader's language. On the installed
// app a Thai screen read the built-in skills ("Excel Spreadsheet Builder —
// Create and edit Excel workbooks…") and the connector cards ("Read and create
// events in your Google Calendar", "productivity") in English: the host writes
// them, and the page showed them as received. Known things are now named by
// this app; anything it does not know is still shown as the host wrote it.
import { describe, expect, it } from "vitest";

import { dictionaries, type DictKey } from "@/lib/i18n/dict";

import { catalogText, connectorStateText, skillText } from "./hostCatalogText";

const t = (locale: "en" | "th") => (key: DictKey) => dictionaries[locale][key];
const THAI = /[ก-๙]/;
// what the host sends (SranDesktop's SkillManager and McpConnectorService)
const BUILTINS = [
  { id: "docx", name: "Word Document Creator", description: "Create professional Word documents with structured formatting" },
  { id: "xlsx", name: "Excel Spreadsheet Builder", description: "Create and edit Excel workbooks with sheets, tables and charts" },
  { id: "pdf", name: "PDF Generator", description: "Generate polished PDF reports and documents" },
  { id: "pptx", name: "Presentation Builder", description: "Create PowerPoint decks with structured slides" },
  { id: "web-search", name: "Web Research", description: "Search the web and cite sources properly" },
];
const CATALOGUE = [
  { id: "slack", category: "communication", description: "Send messages, read channels, search your Slack workspace" },
  { id: "gmail", category: "communication", description: "Read, search and draft emails in your Gmail account" },
  { id: "gdrive", category: "productivity", description: "Search and read documents in your Google Drive" },
  { id: "notion", category: "productivity", description: "Search and read pages in your Notion workspace" },
  { id: "github", category: "developer", description: "Search repos, read issues and pull requests" },
  { id: "jira", category: "developer", description: "Search and update issues in your Jira projects" },
  { id: "gcal", category: "productivity", description: "Read and create events in your Google Calendar" },
  { id: "postgres", category: "data", description: "Run read-only queries against a PostgreSQL database" },
];

describe("a built-in skill", () => {
  it("is named and described in Thai on a Thai screen, every one of the five", () => {
    for (const s of BUILTINS) {
      const shown = skillText({ ...s, isBuiltin: true }, t("th"));
      expect(shown.name, s.id).toMatch(THAI);
      expect(shown.description, s.id).toMatch(THAI);
      expect(shown.name).not.toBe(s.name);
    }
  });

  it("reads exactly as the host wrote it on an English screen", () => {
    for (const s of BUILTINS) expect(skillText({ ...s, isBuiltin: true }, t("en"))).toEqual({ name: s.name, description: s.description });
  });

  it("is known by its English name when a host gives it another id", () => {
    const shown = skillText({ id: "builtin:xlsx-7", name: "Excel Spreadsheet Builder", description: BUILTINS[1].description, isBuiltin: true }, t("th"));
    expect(shown.name).toBe(dictionaries.th["skills.builtin.xlsx.name"]);
  });

  it("keeps the host's own description when it is not the one this app knows", () => {
    const shown = skillText({ id: "xlsx", name: "Excel Spreadsheet Builder", description: "Now also pivots", isBuiltin: true }, t("th"));
    expect(shown.description).toBe("Now also pivots");
  });

  it("never renames a person's own skill, whatever it is called", () => {
    const mine = { id: "xlsx", name: "Excel Spreadsheet Builder", description: "Create PowerPoint decks with structured slides", isBuiltin: false };
    expect(skillText(mine, t("th"))).toEqual({ name: mine.name, description: mine.description });
  });
});

describe("a catalogue connector", () => {
  it("says what it does, and its category, in Thai on a Thai screen — all eight", () => {
    for (const c of CATALOGUE) {
      const shown = catalogText(c, t("th"));
      expect(shown.description, c.id).toMatch(THAI);
      expect(shown.category, c.id).toMatch(THAI);
    }
  });

  it("reads exactly as the catalogue wrote it on an English screen", () => {
    for (const c of CATALOGUE) expect(catalogText(c, t("en"))).toEqual({ description: c.description, category: c.category });
  });

  it("shows a sentence and a category this app does not know as they were sent", () => {
    expect(catalogText({ description: "Query your warehouse", category: "analytics" }, t("th"))).toEqual({ description: "Query your warehouse", category: "analytics" });
  });
});

describe("a connector's state", () => {
  it("is said in the reader's language for the three the host sends, and shown as sent otherwise", () => {
    expect(connectorStateText("NotConfigured", t("th"))).toBe("ยังไม่ได้ตั้งค่า");
    expect(connectorStateText("NotConfigured", t("en"))).toBe("Not set up");
    expect(connectorStateText("Connected", t("th"))).toBe(dictionaries.th["connectors.status.connected"]);
    expect(connectorStateText("Disconnected", t("th"))).toBe(dictionaries.th["connectors.status.disconnected"]);
    expect(connectorStateText("Throttled", t("th"))).toBe("Throttled");
  });
});
