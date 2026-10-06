"use client";

// The mapping screen for the customer's own template (AM2 Phase E), shown
// once the upload's inspect job has read the file. One row per place the
// template takes a value (a slot): the agent's fields for a single place, its
// lists for a repeating row or a table range — and then one select per
// column, from that list's columns. The proposal (and, for a new version,
// the saved choice of every kept slot) is preselected; "— ไม่ใช้ —" leaves a
// place as it is. A place that cannot be filled is listed disabled with the
// reason — nothing is dropped silently. Save sends only the mapped entries
// (./mapping.ts); the server checks them again (400 BAD_MAPPING, shown).

import { useState } from "react";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { SavedTemplate, TemplateInspect, TemplateSlot, TemplateSpec } from "../types";
import { cls, ErrorPanel, Pill } from "../ui";
import {
  buildMapping,
  chooseList,
  columnKey,
  columnOptions,
  columnTitle,
  diffCounts,
  fieldOptions,
  initialSelection,
  isListSlot,
  kindKey,
  listOptions,
  reasonKey,
  saveBody,
  slotTitle,
  type Selection,
} from "./mapping";

const SELECT =
  "focus-ring w-full min-w-0 rounded-md border border-border bg-bg-surface px-2.5 py-1.5 text-sm text-text-primary disabled:cursor-not-allowed disabled:opacity-60";

export function TemplateMapper({
  installId,
  spec,
  fileId,
  fileName,
  inspect,
  saved,
  save: saveTo,
  onSaved,
  onCancel,
}: {
  installId: string;
  spec: TemplateSpec;
  fileId: string;
  fileName: string;
  inspect: TemplateInspect;
  /** The template saved before (its slots name what a new version removed). */
  saved: SavedTemplate | null;
  /** AM10: where the mapping is saved when it is not the install's (a form for one round). */
  save?: (body: ReturnType<typeof saveBody>) => Promise<unknown>;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const t = useAmT();
  const slots = inspect.slots ?? [];
  const [sel, setSel] = useState<Selection>(() => initialSelection(slots, inspect.proposal, spec));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mapping = buildMapping(slots, sel, spec);
  // AM8: what the filler cannot do stops this template before any work, with its reasons
  const blocking = slots.filter((s) => s.blocking);
  const mapped = slots.filter((s) => s.fillable && mapping[s.key]).length;
  const fillable = slots.filter((s) => s.fillable).length;
  const counts = diffCounts(inspect.diff);
  const added = new Set(counts ? inspect.diff.added ?? [] : []);
  const savedSlots = new Map((saved?.slots ?? []).map((s) => [s.key, s] as const));
  const removed = counts
    ? (inspect.diff.removed ?? []).map((k) => {
        const s = savedSlots.get(k);
        return s ? slotTitle(s) : k;
      })
    : [];

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const body = saveBody(fileId, slots, sel, spec);
      if (saveTo) await saveTo(body);
      else await amApi.saveTemplate(installId, body);
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  };

  return (
    <div data-testid="am-template-mapper">
      <h3 className="text-sm font-semibold text-text-primary">{t("tpl.map.title")}</h3>
      <p className="mt-0.5 text-xs text-text-secondary">{t("tpl.map.body")}</p>
      <p className="mt-2 truncate text-xs text-text-muted" title={fileName}>
        {fileName}
      </p>
      {counts ? (
        <p className="mt-2 rounded-lg border border-border-subtle bg-bg-base px-3 py-2 text-xs text-text-secondary" data-testid="am-template-diff">
          {t("tpl.map.diff", { kept: String(counts.kept), added: String(counts.added), removed: String(counts.removed) })}
        </p>
      ) : null}

      {blocking.length ? (
        <div className="mt-2 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-text-primary" role="alert" data-testid="am-template-blocking">
          <p className="font-medium">{t("tpl.map.blocking")}</p>
          <ul className="mt-1 list-disc pl-4">
            {blocking.map((s) => (
              <li key={s.key}>{`${s.label} — ${t(reasonKey(s.reason))}`}</li>
            ))}
          </ul>
        </div>
      ) : inspect.proposed_by ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="am-template-proposed-by">
          {t("tpl.map.proposedBy", { model: inspect.proposed_by })}
        </p>
      ) : null}

      {slots.length ? (
        <ul className="mt-3 flex flex-col gap-2">
          {slots.map((s) => (
            <SlotRow
              key={s.key}
              slot={s}
              spec={spec}
              sel={sel}
              isNew={added.has(s.key)}
              disabled={saving}
              onField={(v) => setSel((cur) => ({ ...cur, [s.key]: v }))}
              onList={(v) => setSel((cur) => chooseList(cur, s, v, spec))}
              onColumn={(c, v) => setSel((cur) => ({ ...cur, [columnKey(s.key, c)]: v }))}
            />
          ))}
        </ul>
      ) : (
        <p className="mt-3 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-text-primary" data-testid="am-template-empty">
          {t("tpl.map.empty")}
        </p>
      )}

      {removed.length ? (
        <div className="mt-3" data-testid="am-template-removed">
          <div className="text-xs font-medium text-text-secondary">{t("tpl.map.removed")}</div>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {removed.map((title) => (
              <li key={title} className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] text-text-secondary line-through">
                {title}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {slots.length ? (
        <p className="mt-3 text-xs text-text-muted" data-testid="am-template-count">
          {t("tpl.map.count", { n: String(mapped), total: String(fillable) })}
          {mapped === 0 && fillable > 0 ? ` · ${t("tpl.map.needOne")}` : null}
        </p>
      ) : null}
      {error ? (
        <div className="mt-3">
          <ErrorPanel message={error} />
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {slots.length ? (
          <Button type="button" size="sm" onClick={() => void save()} loading={saving} disabled={mapped === 0 || blocking.length > 0} data-testid="am-template-save">
            <span>{saving ? t("common.saving") : t("tpl.map.save")}</span>
          </Button>
        ) : null}
        <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={saving} data-testid="am-template-cancel">
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}

function SlotRow({
  slot,
  spec,
  sel,
  isNew,
  disabled,
  onField,
  onList,
  onColumn,
}: {
  slot: TemplateSlot;
  spec: TemplateSpec;
  sel: Selection;
  isNew: boolean;
  disabled: boolean;
  onField: (v: string) => void;
  onList: (v: string) => void;
  onColumn: (column: string, v: string) => void;
}) {
  const t = useAmT();
  const title = slotTitle(slot);
  const kind = kindKey(slot.kind);
  const list = isListSlot(slot);
  const value = slot.fillable ? sel[slot.key] ?? "" : "";
  const options = list ? listOptions(spec) : fieldOptions(spec);
  const columns = list && slot.fillable ? slot.columns ?? [] : [];

  return (
    <li
      className={cls("rounded-lg border px-3 py-2", slot.fillable ? "border-border bg-bg-base" : "border-border-subtle bg-bg-base")}
      data-testid="am-template-slot"
      data-key={slot.key}
      data-fillable={slot.fillable ? "true" : "false"}
    >
      <div className="grid items-center gap-x-3 gap-y-1.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,15rem)]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cls("min-w-0 break-all text-sm", slot.fillable ? "text-text-primary" : "text-text-muted")}>{title}</span>
            {isNew ? (
              <span data-testid="am-template-new">
                <Pill tone="success">{t("tpl.map.new")}</Pill>
              </span>
            ) : null}
          </div>
          {kind ? <div className="text-[11px] text-text-muted">{t(kind)}</div> : null}
          {!slot.fillable ? (
            <p className="mt-0.5 flex items-start gap-1.5 text-xs text-text-secondary" data-testid="am-template-reason">
              <Lock width={12} height={12} className="mt-0.5 shrink-0 text-status-warning" aria-hidden />
              <span>{t(reasonKey(slot.reason), { code: slot.reason ?? "" })}</span>
            </p>
          ) : null}
        </div>
        <select
          value={value}
          disabled={!slot.fillable || disabled}
          onChange={(e) => (list ? onList(e.target.value) : onField(e.target.value))}
          aria-label={t("tpl.map.slotAria", { slot: title })}
          className={SELECT}
          data-testid="am-template-select"
        >
          <option value="">{slot.fillable ? t("tpl.map.none") : t("tpl.map.cantFill")}</option>
          {slot.fillable
            ? options.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))
            : null}
        </select>
      </div>
      {columns.length ? (
        <ul className="mt-2 flex flex-col gap-1.5 border-l border-border-subtle pl-3" data-testid="am-template-columns">
          {columns.map((c) => {
            // AM12: a column of a table of items in a form with no marker is named by the heading over it
            const heading = slot.column_labels?.[c];
            const colTitle = heading ? t("tpl.map.columnOf", { label: heading }) : columnTitle(slot, c) ?? t("tpl.map.column", { col: c });
            return (
              <li key={c} className="grid items-center gap-x-3 gap-y-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,15rem)]">
                <span className="min-w-0 break-all text-xs text-text-secondary">{colTitle}</span>
                <select
                  value={value ? sel[columnKey(slot.key, c)] ?? "" : ""}
                  disabled={!value || disabled}
                  onChange={(e) => onColumn(c, e.target.value)}
                  aria-label={t("tpl.map.colAria", { col: colTitle, slot: title })}
                  className={SELECT}
                  data-testid="am-template-column"
                >
                  <option value="">{t("tpl.map.none")}</option>
                  {value
                    ? columnOptions(spec, value).map((o) => (
                        <option key={o.key} value={o.key}>
                          {o.label}
                        </option>
                      ))
                    : null}
                </select>
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}
