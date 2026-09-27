"use client";

// The expense policy table and the holiday calendar as a person edits them
// (AM2 phase F). Controlled: the wizard step and the settings section own
// the draft and save it. A field's problem shows once it has something in
// it, and every problem once a save was tried (`showAll`).

import { Plus, Trash2 } from "lucide-react";

import { useAmT, type AmKey } from "../i18n";
import { cls } from "../ui";
import {
  edited,
  emptyCategory,
  rowId,
  VAT_DOCUMENTS,
  WEEKEND_RULES,
  type CategoryDraft,
  type HolidayRow,
  type PolicyDraft,
  type PolicyProblems,
} from "./policy";

const FIELD =
  "focus-ring w-full min-w-0 rounded-lg border bg-bg-base px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted disabled:opacity-60";

function Problem({ id, k }: { id: string; k: AmKey | null | undefined }) {
  const t = useAmT();
  if (!k) return null;
  return (
    <span id={id} role="alert" className="mt-1 block text-xs text-status-danger" data-testid="am-policy-error">
      {t(k)}
    </span>
  );
}

function Money({
  id,
  label,
  value,
  problem,
  placeholder,
  hint,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  problem: AmKey | null;
  placeholder?: string;
  hint?: string;
  disabled?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <div className="min-w-0 text-sm">
      <label htmlFor={id} className="text-xs text-text-secondary">
        {label}
      </label>
      <input
        id={id}
        value={value}
        disabled={disabled}
        inputMode="decimal"
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-invalid={!!problem}
        aria-describedby={problem ? `${id}-err` : undefined}
        className={cls(FIELD, "mt-1 tabular-nums", problem ? "border-status-danger" : "border-border")}
      />
      {hint ? <span className="mt-1 block text-[11px] text-text-muted">{hint}</span> : null}
      <Problem id={`${id}-err`} k={problem} />
    </div>
  );
}

export function PolicyEditor({
  draft,
  onChange,
  problems,
  showAll,
  disabled = false,
  idPrefix = "am-policy",
}: {
  draft: PolicyDraft;
  onChange: (d: PolicyDraft) => void;
  problems: PolicyProblems;
  showAll: boolean;
  disabled?: boolean;
  idPrefix?: string;
}) {
  const t = useAmT();
  const set = (patch: Partial<PolicyDraft>) => onChange(edited(draft, patch));
  const setCat = (id: string, patch: Partial<CategoryDraft>) =>
    set({ categories: draft.categories.map((c) => (c.id === id ? { ...c, ...patch } : c)) });
  // a problem shows once the field has something in it, or once a save was tried
  const shown = (key: string, value: string) => (showAll || value.trim() ? problems[key] ?? null : null);
  const id = (s: string) => `${idPrefix}-${s}`;

  return (
    <div className="flex flex-col gap-5" data-testid="am-policy-editor">
      {draft.source === "package_sample" ? (
        <p className="rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-text-primary" data-testid="am-policy-sample">
          {t("policy.sample")}
        </p>
      ) : null}
      <p className="text-xs text-text-secondary" data-testid="am-policy-only-source">
        {t("policy.onlySource")}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <Money
          id={id("receipt")}
          label={t("policy.receiptAbove")}
          value={draft.receipt_required_above}
          problem={shown("receipt_required_above", draft.receipt_required_above)}
          disabled={disabled}
          onChange={(v) => set({ receipt_required_above: v })}
        />
        <Money
          id={id("approval")}
          label={t("policy.approval")}
          hint={t("policy.approvalHint")}
          value={draft.approval_threshold}
          problem={shown("approval_threshold", draft.approval_threshold)}
          disabled={disabled}
          onChange={(v) => set({ approval_threshold: v })}
        />
      </div>

      <fieldset disabled={disabled}>
        <legend className="text-sm font-medium text-text-primary">{t("policy.categories")}</legend>
        <ul className="mt-2 flex flex-col gap-2.5">
          {draft.categories.map((c) => (
            <CategoryCard
              key={c.id}
              c={c}
              problems={problems}
              showAll={showAll}
              idPrefix={idPrefix}
              onPatch={(patch) => setCat(c.id, patch)}
              onRemove={() => set({ categories: draft.categories.filter((x) => x.id !== c.id) })}
            />
          ))}
        </ul>
        {showAll && problems.categories ? <Problem id={id("categories-err")} k={problems.categories} /> : null}
        <button
          type="button"
          onClick={() => set({ categories: [...draft.categories, emptyCategory()] })}
          className="focus-ring mt-2 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
          data-testid="am-policy-add-category"
        >
          <Plus width={14} height={14} aria-hidden />
          <span>{t("policy.addCategory")}</span>
        </button>
      </fieldset>

      <fieldset disabled={disabled}>
        <legend className="text-sm font-medium text-text-primary">{t("policy.weekend")}</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {WEEKEND_RULES.map((rule) => (
            <label
              key={rule}
              className={cls(
                "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm",
                draft.weekend_holiday === rule ? "border-text-primary bg-bg-elevated" : "border-border bg-bg-base hover:bg-bg-elevated",
              )}
            >
              <input
                type="radio"
                name={id("weekend")}
                checked={draft.weekend_holiday === rule}
                onChange={() => set({ weekend_holiday: rule })}
                className="mt-0.5 h-4 w-4 accent-current"
                data-testid={`am-policy-weekend-${rule}`}
              />
              <span className="flex min-w-0 flex-col">
                <span className="text-text-primary">{t(`policy.weekend.${rule}` as AmKey)}</span>
                <span className="text-xs text-text-muted">{t(`policy.weekend.${rule}Hint` as AmKey)}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="text-sm">
          <label htmlFor={id("vat")} className="text-xs text-text-secondary">
            {t("policy.vat")}
          </label>
          <select
            id={id("vat")}
            value={draft.vat_claim_requires}
            disabled={disabled}
            onChange={(e) => set({ vat_claim_requires: e.target.value as PolicyDraft["vat_claim_requires"] })}
            className={cls(FIELD, "mt-1 border-border")}
          >
            {VAT_DOCUMENTS.map((v) => (
              <option key={v} value={v}>
                {t(`policy.vat.${v}` as AmKey)}
              </option>
            ))}
          </select>
        </div>
        <div className="text-sm">
          <label htmlFor={id("prohibited")} className="text-xs text-text-secondary">
            {t("policy.prohibited")}
          </label>
          <textarea
            id={id("prohibited")}
            rows={3}
            value={draft.prohibited}
            disabled={disabled}
            onChange={(e) => set({ prohibited: e.target.value })}
            className={cls(FIELD, "mt-1 resize-y border-border")}
            data-testid="am-policy-prohibited"
          />
          <span className="mt-1 block text-[11px] text-text-muted">{t("policy.prohibitedHint")}</span>
        </div>
      </div>
    </div>
  );
}

export function HolidayEditor({
  rows,
  onChange,
  problems,
  showAll,
  disabled = false,
  idPrefix = "am-holidays",
}: {
  rows: HolidayRow[];
  onChange: (rows: HolidayRow[]) => void;
  problems: Record<string, AmKey>;
  showAll: boolean;
  disabled?: boolean;
  idPrefix?: string;
}) {
  const t = useAmT();
  const setRow = (rid: string, patch: Partial<HolidayRow>) => onChange(rows.map((r) => (r.id === rid ? { ...r, ...patch } : r)));
  return (
    <fieldset disabled={disabled} data-testid="am-holidays">
      <legend className="text-sm font-medium text-text-primary">{t("policy.holidays")}</legend>
      <p className="mt-0.5 text-xs text-text-muted">{t("policy.holidaysDefault")}</p>
      {rows.length ? (
        <ul className="mt-2 flex flex-col gap-2">
          {rows.map((r) => {
            const dateProblem = showAll || r.date ? problems[`${r.id}.date`] ?? null : null;
            const nameProblem = showAll ? problems[`${r.id}.name`] ?? null : null;
            return (
              <li key={r.id} className="grid items-start gap-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto]" data-testid="am-holiday-row">
                <div className="min-w-0">
                  <input
                    type="date"
                    value={r.date}
                    onChange={(e) => setRow(r.id, { date: e.target.value })}
                    aria-label={t("policy.holidayDate")}
                    aria-invalid={!!dateProblem}
                    className={cls(FIELD, "tabular-nums", dateProblem ? "border-status-danger" : "border-border")}
                    data-testid="am-holiday-date"
                  />
                  <Problem id={`${idPrefix}-${r.id}-date-err`} k={dateProblem} />
                </div>
                <div className="min-w-0">
                  <input
                    value={r.name}
                    onChange={(e) => setRow(r.id, { name: e.target.value })}
                    aria-label={t("policy.holidayName")}
                    placeholder={t("policy.holidayName")}
                    aria-invalid={!!nameProblem}
                    className={cls(FIELD, nameProblem ? "border-status-danger" : "border-border")}
                    data-testid="am-holiday-name"
                  />
                  <Problem id={`${idPrefix}-${r.id}-name-err`} k={nameProblem} />
                </div>
                <button
                  type="button"
                  onClick={() => onChange(rows.filter((x) => x.id !== r.id))}
                  aria-label={t("policy.removeHoliday", { date: r.date || "—" })}
                  className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                >
                  <Trash2 width={15} height={15} aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      <button
        type="button"
        onClick={() => onChange([...rows, { id: rowId("hol"), date: "", name: "" }])}
        className="focus-ring mt-2 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
        data-testid="am-holiday-add"
      >
        <Plus width={14} height={14} aria-hidden />
        <span>{t("policy.addHoliday")}</span>
      </button>
    </fieldset>
  );
}

function CategoryCard({
  c,
  problems,
  showAll,
  idPrefix,
  onPatch,
  onRemove,
}: {
  c: CategoryDraft;
  problems: PolicyProblems;
  showAll: boolean;
  idPrefix: string;
  onPatch: (patch: Partial<CategoryDraft>) => void;
  onRemove: () => void;
}) {
  const t = useAmT();
  const id = (s: string) => `${idPrefix}-${s}`;
  const k = (f: keyof CategoryDraft) => `cat.${c.id}.${f}`;
  const shown = (key: string, value: string) => (showAll || value.trim() ? problems[key] ?? null : null);
  const codeProblem = shown(k("code"), c.code);
  // a WHT rate without its starting amount says so at once
  const whtFromProblem = shown(k("wht_from"), c.wht_from) ?? (c.wht_rate.trim() ? problems[k("wht_from")] ?? null : null);
  return (
    <li className="rounded-xl border border-border bg-bg-base px-3 py-3" data-testid="am-policy-category" data-code={c.code}>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto]">
        <div className="min-w-0 text-sm">
          <label htmlFor={id(`${c.id}-code`)} className="text-xs text-text-secondary">
            {t("policy.code")}
          </label>
          <input
            id={id(`${c.id}-code`)}
            value={c.code}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => onPatch({ code: e.target.value })}
            aria-invalid={!!codeProblem}
            aria-describedby={codeProblem ? id(`${c.id}-code-err`) : undefined}
            className={cls(FIELD, "mt-1 font-mono", codeProblem ? "border-status-danger" : "border-border")}
            data-testid="am-policy-code"
          />
          <Problem id={id(`${c.id}-code-err`)} k={codeProblem} />
        </div>
        <div className="min-w-0 text-sm">
          <label htmlFor={id(`${c.id}-label`)} className="text-xs text-text-secondary">
            {t("policy.label")}
          </label>
          <input
            id={id(`${c.id}-label`)}
            value={c.label}
            onChange={(e) => onPatch({ label: e.target.value })}
            className={cls(FIELD, "mt-1 border-border")}
          />
        </div>
        <div className="flex items-end">
          <button
            type="button"
            onClick={onRemove}
            aria-label={t("policy.removeCategory", { code: c.code || c.label || "—" })}
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          >
            <Trash2 width={15} height={15} aria-hidden />
          </button>
        </div>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Money
          id={id(`${c.id}-per-claim`)}
          label={t("policy.perClaim")}
          placeholder={t("policy.noLimit")}
          value={c.per_claim}
          problem={shown(k("per_claim"), c.per_claim)}
          onChange={(v) => onPatch({ per_claim: v })}
        />
        <Money
          id={id(`${c.id}-per-month`)}
          label={t("policy.perMonth")}
          placeholder={t("policy.noLimit")}
          value={c.per_person_month}
          problem={shown(k("per_person_month"), c.per_person_month)}
          onChange={(v) => onPatch({ per_person_month: v })}
        />
        <Money
          id={id(`${c.id}-wht-rate`)}
          label={t("policy.whtRate")}
          value={c.wht_rate}
          problem={shown(k("wht_rate"), c.wht_rate)}
          onChange={(v) => onPatch({ wht_rate: v })}
        />
        <Money
          id={id(`${c.id}-wht-from`)}
          label={t("policy.whtFrom")}
          value={c.wht_from}
          problem={whtFromProblem}
          onChange={(v) => onPatch({ wht_from: v })}
        />
      </div>
      <label className="mt-2.5 flex cursor-pointer items-center gap-2 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={c.needs_purpose}
          onChange={(e) => onPatch({ needs_purpose: e.target.checked })}
          className="h-4 w-4 accent-current"
        />
        <span>{t("policy.needsPurpose")}</span>
      </label>
    </li>
  );
}
