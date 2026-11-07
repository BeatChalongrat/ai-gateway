"use client";

// The company an agent checks tax invoices against (install question
// "company_tax", AM2 invoice agent): its name, 13-digit tax ID and head office
// or branch, kept as the install defaults company, company_tax_id and
// company_branch. The install step and the settings page share these fields.
// The tax ID is checked here (mod-11) before it is saved; the package reads
// it only to see whether an invoice is made out to this company.

import { useState } from "react";

import { amApi } from "../api";
import { useAmT, type AmKey } from "../i18n";
import type { Default } from "../types";
import { checkBranch, checkTaxId, HEAD_OFFICE, normalizeTaxId } from "../typed";
import { cls } from "../ui";

export interface CompanyTaxDraft {
  company: string;
  taxId: string;
  head: boolean;
  branch: string;
}

export const EMPTY_COMPANY_TAX: CompanyTaxDraft = { company: "", taxId: "", head: true, branch: "" };

/** What is saved: company, company_tax_id (13 digits), company_branch ("00000" = head office). */
export interface CompanyTaxValue {
  company: string;
  company_tax_id: string;
  company_branch: string;
}

export const COMPANY_TAX_KEYS = ["company", "company_tax_id", "company_branch"] as const;

function stringValue(defaults: Default[] | null | undefined, key: string): string {
  const v = (defaults ?? []).find((d) => d.key === key)?.value;
  return typeof v === "string" ? v : typeof v === "number" ? String(v) : "";
}

export function draftFromDefaults(defaults: Default[] | null | undefined): CompanyTaxDraft {
  const branch = stringValue(defaults, "company_branch");
  const head = !branch || branch === HEAD_OFFICE;
  return { company: stringValue(defaults, "company"), taxId: stringValue(defaults, "company_tax_id"), head, branch: head ? "" : branch };
}

export interface CompanyTaxCheck {
  company: AmKey | null;
  taxId: AmKey | null;
  branch: AmKey | null;
  /** Set only when every field is valid. */
  value: CompanyTaxValue | null;
}

export function checkCompanyTax(d: CompanyTaxDraft): CompanyTaxCheck {
  const company = d.company.trim();
  const tax = checkTaxId(d.taxId);
  const branch = d.head ? null : checkBranch(d.branch);
  const out: CompanyTaxCheck = {
    company: company ? null : "companyTax.err.company",
    // an empty tax ID reads better as "13 digits needed" than "nothing typed"
    taxId: tax.ok ? null : tax.reason === "typed.err.empty" ? "typed.err.taxIdLength" : tax.reason,
    branch: branch && !branch.ok ? branch.reason : null,
    value: null,
  };
  if (company && tax.ok && (!branch || branch.ok)) {
    out.value = { company, company_tax_id: tax.value, company_branch: branch?.ok ? branch.value : HEAD_OFFICE };
  }
  return out;
}

export async function saveCompanyTax(installId: string, v: CompanyTaxValue): Promise<void> {
  await amApi.putDefault(installId, "company", v.company);
  await amApi.putDefault(installId, "company_tax_id", v.company_tax_id);
  await amApi.putDefault(installId, "company_branch", v.company_branch);
}

function Required() {
  const t = useAmT();
  return (
    <>
      <span aria-hidden className="ml-0.5 text-status-danger">
        *
      </span>
      <span className="sr-only">{` (${t("common.required")})`}</span>
    </>
  );
}

function FieldError({ id, reason }: { id: string; reason: AmKey | null }) {
  const t = useAmT();
  if (!reason) return null;
  return (
    <span id={id} role="alert" className="mt-1 block text-xs text-status-danger" data-testid="am-field-error">
      {t(reason)}
    </span>
  );
}

export function CompanyTaxFields({
  draft,
  onChange,
  idPrefix = "am-company-tax",
  disabled = false,
}: {
  draft: CompanyTaxDraft;
  onChange: (d: CompanyTaxDraft) => void;
  idPrefix?: string;
  disabled?: boolean;
}) {
  const t = useAmT();
  const [touched, setTouched] = useState<Partial<Record<"company" | "taxId" | "branch", boolean>>>({});
  const check = checkCompanyTax(draft);
  const set = (p: Partial<CompanyTaxDraft>) => onChange({ ...draft, ...p });
  const touch = (k: "company" | "taxId" | "branch") => () => setTouched((s) => (s[k] ? s : { ...s, [k]: true }));
  // A full 13 digits is judged at once; anything shorter once the field is left.
  const taxShown = touched.taxId || normalizeTaxId(draft.taxId).length >= 13;
  const companyErr = touched.company ? check.company : null;
  const taxErr = taxShown ? check.taxId : null;
  const branchErr = touched.branch ? check.branch : null;
  const id = (s: string) => `${idPrefix}-${s}`;
  const field = (bad: boolean) =>
    cls(
      "focus-ring mt-1 w-full rounded-lg border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted disabled:opacity-60",
      bad ? "border-status-danger" : "border-border",
    );

  return (
    <div className="flex flex-col gap-4" data-testid="am-company-tax-fields">
      <div className="text-sm">
        <label htmlFor={id("company")} className="text-text-secondary">
          {t("companyTax.company")}
          <Required />
        </label>
        <input
          id={id("company")}
          value={draft.company}
          disabled={disabled}
          onChange={(e) => set({ company: e.target.value })}
          onBlur={touch("company")}
          maxLength={120}
          placeholder={t("companyTax.companyPlaceholder")}
          aria-invalid={!!companyErr}
          aria-describedby={companyErr ? id("company-err") : undefined}
          className={field(!!companyErr)}
          data-testid="am-company-name"
        />
        <FieldError id={id("company-err")} reason={companyErr} />
      </div>

      <div className="text-sm">
        <label htmlFor={id("tax")} className="text-text-secondary">
          {t("companyTax.taxId")}
          <Required />
        </label>
        <input
          id={id("tax")}
          value={draft.taxId}
          disabled={disabled}
          inputMode="numeric"
          autoComplete="off"
          maxLength={20}
          onChange={(e) => set({ taxId: e.target.value })}
          onBlur={touch("taxId")}
          placeholder={t("companyTax.taxIdPlaceholder")}
          aria-invalid={!!taxErr}
          aria-describedby={cls(id("tax-use"), taxErr ? id("tax-err") : "")}
          className={cls(field(!!taxErr), "tabular-nums")}
          data-testid="am-company-tax-id"
        />
        <span id={id("tax-use")} className="mt-1 block text-xs text-text-muted">
          {t("companyTax.taxIdUse")}
        </span>
        <FieldError id={id("tax-err")} reason={taxErr} />
      </div>

      <fieldset className="text-sm" disabled={disabled}>
        <legend className="text-text-secondary">{t("companyTax.branch")}</legend>
        <div className="mt-1 grid gap-2 sm:grid-cols-2">
          {[true, false].map((head) => (
            <label
              key={String(head)}
              className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-bg-base px-3 py-2.5 text-text-primary hover:bg-bg-elevated"
            >
              <input
                type="radio"
                name={id("branch-kind")}
                checked={draft.head === head}
                onChange={() => set({ head })}
                className="h-4 w-4 accent-current"
                data-testid={head ? "am-company-head" : "am-company-branch"}
              />
              <span>{head ? t("companyTax.head") : t("companyTax.branchOpt")}</span>
            </label>
          ))}
        </div>
        {!draft.head ? (
          <div className="mt-2">
            <label htmlFor={id("branch")} className="text-text-secondary">
              {t("companyTax.branchNo")}
              <Required />
            </label>
            <input
              id={id("branch")}
              value={draft.branch}
              inputMode="numeric"
              autoComplete="off"
              maxLength={5}
              onChange={(e) => set({ branch: e.target.value })}
              onBlur={touch("branch")}
              placeholder={t("companyTax.branchPlaceholder")}
              aria-invalid={!!branchErr}
              aria-describedby={branchErr ? id("branch-err") : undefined}
              className={cls(field(!!branchErr), "tabular-nums sm:w-40")}
              data-testid="am-company-branch-no"
            />
            <FieldError id={id("branch-err")} reason={branchErr} />
          </div>
        ) : null}
      </fieldset>
    </div>
  );
}
