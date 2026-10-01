// The customer's own template (AM2 Phase E): the mapping screen's rules,
// kept pure so they are tested without a screen.
//
// A mapping (install default "template".mapping) sends a template slot to
// one of the agent's values: "slot" → a field key (a single place) or a
// list key (a list or table slot), and "slot.column" → "list.column" for
// each column of a list or table slot. The screen keeps a Selection keyed
// the same way ("" = not used); buildMapping turns it into what PUT sends —
// only mapped entries, only ones the server will accept
// (agentmarket.CheckTemplateMapping).

import type { AmKey } from "../i18n";
import type { Default, SavedTemplate, TemplateDiff, TemplateSlot, TemplateSpec } from "../types";

export type Selection = Record<string, string>;

export interface Option {
  key: string;
  label: string;
}

export const LIST_KINDS = new Set(["list", "table"]);

export const isListSlot = (s: Pick<TemplateSlot, "kind">) => LIST_KINDS.has(s.kind);

/** The key of a list or table slot's column in the mapping. */
export const columnKey = (slotKey: string, column: string) => `${slotKey}.${column}`;

/** The agent's single values, in manifest order. */
export function fieldOptions(spec: TemplateSpec | null | undefined): Option[] {
  return Object.entries(spec?.fields ?? {}).map(([key, f]) => ({
    key,
    label: (typeof f === "string" ? f : f?.label) || key,
  }));
}

/** The agent's lists (rows for a repeating row or a table range). */
export function listOptions(spec: TemplateSpec | null | undefined): Option[] {
  return Object.entries(spec?.lists ?? {}).map(([key, l]) => ({ key, label: l?.label || key }));
}

/** One list's columns, as "list.column" options. */
export function columnOptions(spec: TemplateSpec | null | undefined, listKey: string): Option[] {
  const cols = spec?.lists?.[listKey]?.columns ?? {};
  return Object.entries(cols).map(([col, label]) => ({ key: `${listKey}.${col}`, label: label || col }));
}

/** Does this agent fill templates at all (manifest.templates with a field or a list)? */
export function takesTemplates(spec: TemplateSpec | null | undefined): boolean {
  return fieldOptions(spec).length + listOptions(spec).length > 0;
}

const norm = (s: string) => s.toLowerCase().replace(/[^0-9a-zก-๙]+/g, "");

/** A list column whose key or label is the template column's own name ({{line.date}} → lines.date), or "". */
export function matchColumn(spec: TemplateSpec | null | undefined, listKey: string, column: string): string {
  const want = norm(column);
  if (!want) return "";
  const hit = columnOptions(spec, listKey).find((o) => norm(o.key.slice(listKey.length + 1)) === want || norm(o.label) === want);
  return hit?.key ?? "";
}

/** What the screen starts from: the proposal (with the saved choice for kept slots), minus anything the server would refuse. */
export function initialSelection(slots: TemplateSlot[], proposal: Record<string, string> | null | undefined, spec: TemplateSpec | null | undefined): Selection {
  const p = proposal ?? {};
  const fields = new Set(fieldOptions(spec).map((o) => o.key));
  const lists = new Set(listOptions(spec).map((o) => o.key));
  const sel: Selection = {};
  for (const s of slots) {
    if (!s.fillable) continue;
    const v = p[s.key] ?? "";
    if (!isListSlot(s)) {
      sel[s.key] = fields.has(v) ? v : "";
      continue;
    }
    const list = lists.has(v) ? v : "";
    sel[s.key] = list;
    const cols = new Set(list ? columnOptions(spec, list).map((o) => o.key) : []);
    for (const c of s.columns ?? []) {
      const cv = p[columnKey(s.key, c)] ?? "";
      sel[columnKey(s.key, c)] = cols.has(cv) ? cv : "";
    }
  }
  return sel;
}

/** A list or table slot given another list: its columns start over from the names that match. */
export function chooseList(sel: Selection, slot: TemplateSlot, listKey: string, spec: TemplateSpec | null | undefined): Selection {
  const next: Selection = { ...sel, [slot.key]: listKey };
  for (const c of slot.columns ?? []) next[columnKey(slot.key, c)] = listKey ? matchColumn(spec, listKey, c) : "";
  return next;
}

/** The mapping PUT sends: only mapped, fillable entries; a column only with its slot's own list. */
export function buildMapping(slots: TemplateSlot[], sel: Selection, spec: TemplateSpec | null | undefined): Record<string, string> {
  const fields = new Set(fieldOptions(spec).map((o) => o.key));
  const lists = new Set(listOptions(spec).map((o) => o.key));
  const out: Record<string, string> = {};
  for (const s of slots) {
    const v = sel[s.key];
    if (!s.fillable || !v) continue;
    if (!isListSlot(s)) {
      if (fields.has(v)) out[s.key] = v;
      continue;
    }
    if (!lists.has(v)) continue;
    out[s.key] = v;
    const cols = new Set(columnOptions(spec, v).map((o) => o.key));
    for (const c of s.columns ?? []) {
      const cv = sel[columnKey(s.key, c)];
      if (cv && cols.has(cv)) out[columnKey(s.key, c)] = cv;
    }
  }
  return out;
}

/** The body of PUT /installs/{id}/template. */
export function saveBody(fileId: string, slots: TemplateSlot[], sel: Selection, spec: TemplateSpec | null | undefined) {
  return { file_id: fileId, slots, mapping: buildMapping(slots, sel, spec) };
}

/** How many slots have a value chosen (a list counts once, whatever its columns). */
export function mappedCount(slots: TemplateSlot[], mapping: Record<string, string> | null | undefined): number {
  const m = mapping ?? {};
  return slots.filter((s) => s.fillable && !!m[s.key]).length;
}

/** A new version against the saved one, or null for a first template. */
export function diffCounts(diff: TemplateDiff | null | undefined): { kept: number; added: number; removed: number } | null {
  if (!diff || diff.first) return null;
  return { kept: diff.kept?.length ?? 0, added: diff.added?.length ?? 0, removed: diff.removed?.length ?? 0 };
}

/** The slot as the person sees it in the file: {{marker}}, a control's alias, a name or a labelled cell with its reference. */
export function slotTitle(s: TemplateSlot): string {
  switch (s.kind) {
    case "marker":
      return `{{${s.key}}}`;
    case "list":
      return `{{${s.key}.…}}`;
    case "control":
      return s.label || s.key;
  }
  const label = s.label || s.key;
  return s.where && s.where !== label ? `${label} (${s.where})` : label;
}

/** A list's column as written in the file ({{line.date}}); a table's column letter is worded by the screen. */
export function columnTitle(s: TemplateSlot, column: string): string | null {
  return s.kind === "list" ? `{{${s.key}.${column}}}` : null;
}

const KIND_KEYS: Record<string, AmKey> = {
  marker: "tpl.kind.marker",
  list: "tpl.kind.list",
  control: "tpl.kind.control",
  name: "tpl.kind.name",
  table: "tpl.kind.table",
  target: "tpl.kind.target",
};

export function kindKey(kind: string): AmKey | null {
  return KIND_KEYS[kind] ?? null;
}

const REASON_KEYS: Record<string, AmKey> = {
  formula_cell: "tpl.reason.formula_cell",
  protected_sheet: "tpl.reason.protected_sheet",
  not_plain_text: "tpl.reason.not_plain_text",
  merged_cell: "tpl.reason.merged_cell",
  data_bound: "tpl.reason.data_bound",
  content_locked: "tpl.reason.content_locked",
  unknown_name: "tpl.reason.unknown_name",
  unknown_sheet: "tpl.reason.unknown_sheet",
  ambiguous_name: "tpl.reason.ambiguous_name",
  not_a_cell_range: "tpl.reason.not_a_cell_range",
  not_a_single_range: "tpl.reason.not_a_single_range",
  unsupported_location: "tpl.reason.unsupported_location",
  chart_not_updated: "tpl.reason.chart_not_updated",
};

/** Why a slot cannot be filled, in plain words; an unknown code still gets a sentence (never a silent drop). */
export function reasonKey(reason: string | null | undefined): AmKey {
  if (!reason) return "tpl.reason.none";
  return REASON_KEYS[reason] ?? "tpl.reason.other";
}

/** The saved template (install default "template"), or null. */
export function savedTemplateOf(defaults: Default[] | null | undefined): SavedTemplate | null {
  const v = (defaults ?? []).find((d) => d.key === "template")?.value as SavedTemplate | null | undefined;
  return v && typeof v === "object" && typeof v.file_id === "string" && v.file_id ? v : null;
}
