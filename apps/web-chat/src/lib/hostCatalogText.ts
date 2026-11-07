// What the desktop host says about its own catalogue, in the reader's language.
//
// The app's built-in skills and its connector catalogue are written in the
// host, in English: on a Thai screen the skills page read "Excel Spreadsheet
// Builder — Create and edit Excel workbooks…" and a connector card "Read and
// create events in your Google Calendar", under a Thai heading (the walk on
// the installed app found them; a browser has no host and shows neither).
//
// They are known things, so they are named here — the same way a tool's row
// is named by its id (lib/toolLabels). Only what is known is replaced:
//
//   * a built-in skill, by its id (or its English name, which is what an
//     older host identifies it by);
//   * a sentence of the catalogue, when it is word for word the one the
//     dictionary's English entry holds — a catalogue that says something else
//     about the same connector is shown as it wrote it;
//   * a category and a state, by their code.
//
// Anything else the host sends — a person's own skill, a custom connector, a
// connector a newer catalogue adds — is shown as received.

import { dictionaries, type DictKey } from "@/lib/i18n/dict";

type T = (key: DictKey, vars?: Record<string, string>) => string;

const SKILLS: Record<string, { name: DictKey; description: DictKey }> = {
  docx: { name: "skills.builtin.docx.name", description: "skills.builtin.docx.description" },
  xlsx: { name: "skills.builtin.xlsx.name", description: "skills.builtin.xlsx.description" },
  pdf: { name: "skills.builtin.pdf.name", description: "skills.builtin.pdf.description" },
  pptx: { name: "skills.builtin.pptx.name", description: "skills.builtin.pptx.description" },
  "web-search": { name: "skills.builtin.webSearch.name", description: "skills.builtin.webSearch.description" },
};

const CONNECTORS: DictKey[] = [
  "connectors.catalog.slack",
  "connectors.catalog.gmail",
  "connectors.catalog.gdrive",
  "connectors.catalog.notion",
  "connectors.catalog.github",
  "connectors.catalog.jira",
  "connectors.catalog.gcal",
  "connectors.catalog.postgres",
];

const CATEGORIES: Record<string, DictKey> = {
  communication: "connectors.category.communication",
  productivity: "connectors.category.productivity",
  developer: "connectors.category.developer",
  data: "connectors.category.data",
};

const STATES: Record<string, DictKey> = {
  Connected: "connectors.status.connected",
  Disconnected: "connectors.status.disconnected",
  NotConfigured: "connectors.status.notConfigured",
};

const english = (key: DictKey): string => dictionaries.en[key];

function knownSkill(skill: { id: string; name: string; isBuiltin: boolean }) {
  if (!skill.isBuiltin) return undefined;
  return SKILLS[skill.id] ?? Object.values(SKILLS).find((s) => english(s.name) === skill.name);
}

/** A skill's name and description: a built-in one in the reader's language, a person's own as they wrote it. */
export function skillText(skill: { id: string; name: string; description: string; isBuiltin: boolean }, t: T): { name: string; description: string } {
  const known = knownSkill(skill);
  if (!known) return { name: skill.name, description: skill.description };
  return {
    name: t(known.name),
    // the host may describe a built-in in other words one day: then its own words stand
    description: skill.description === english(known.description) ? t(known.description) : skill.description,
  };
}

/** A catalogue connector's sentence and category in the reader's language, when they are the ones this app knows. */
export function catalogText(connector: { description: string; category: string }, t: T): { description: string; category: string } {
  const sentence = CONNECTORS.find((key) => english(key) === connector.description);
  const category = CATEGORIES[connector.category.toLowerCase()];
  return {
    description: sentence ? t(sentence) : connector.description,
    category: category ? t(category) : connector.category,
  };
}

/** A connector's state as the host names it (Connected, Disconnected, NotConfigured); any other is shown as sent. */
export function connectorStateText(status: string, t: T): string {
  const key = STATES[status];
  return key ? t(key) : status;
}
