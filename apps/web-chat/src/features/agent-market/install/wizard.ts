// Which install steps an agent gets: derived from its manifest's
// install.questions. The financial report asks ["company", "theme",
// "formats", "currency", "mapping"] and keeps AM1's five steps; the invoice
// agent asks ["company_tax", "theme", "formats"] and gets company details →
// theme → file set → self-test; the expense audit asks ["company", "policy",
// "theme", "formats"] and gets its policy table → theme (with the company
// name) → file set → self-test. The order is fixed, whatever the manifest's.

import type { AmKey } from "../i18n";
import { takesTemplates } from "../template/mapping";
import type { Manifest } from "../types";

/** What a manifest that declares no questions is asked: AM1's full wizard. */
export const LEGACY_QUESTIONS = ["company", "theme", "formats", "currency", "mapping"];

export function installQuestions(manifest: Manifest | null | undefined): string[] {
  const qs = manifest?.install?.questions;
  return qs?.length ? qs : LEGACY_QUESTIONS;
}

export type StepId = "source" | "mapping" | "company_tax" | "policy" | "theme" | "formats" | "template" | "selftest";

export const STEP_LABEL: Record<StepId, AmKey> = {
  source: "install.step1",
  mapping: "install.step2",
  company_tax: "install.stepCompanyTax",
  policy: "install.stepPolicy",
  theme: "install.step3",
  formats: "install.step4",
  template: "wiz.stepTemplate",
  selftest: "install.step5",
};

/** AM3: every step says why it is asked, and what happens if it is wrong or skipped (and where to fix it). */
export const STEP_WHY: Record<StepId, { why: AmKey; ifWrong: AmKey }> = {
  source: { why: "wiz.why.source", ifWrong: "wiz.ifWrong.source" },
  mapping: { why: "wiz.why.mapping", ifWrong: "wiz.ifWrong.mapping" },
  company_tax: { why: "wiz.why.company_tax", ifWrong: "wiz.ifWrong.company_tax" },
  policy: { why: "wiz.why.policy", ifWrong: "wiz.ifWrong.policy" },
  theme: { why: "wiz.why.theme", ifWrong: "wiz.ifWrong.theme" },
  formats: { why: "wiz.why.formats", ifWrong: "wiz.ifWrong.formats" },
  template: { why: "wiz.why.template", ifWrong: "wiz.ifWrong.template" },
  selftest: { why: "wiz.why.selftest", ifWrong: "wiz.ifWrong.selftest" },
};

export function installSteps(manifest: Manifest | null | undefined): StepId[] {
  const qs = installQuestions(manifest);
  const out: StepId[] = [];
  if (qs.includes("mapping")) out.push("source", "mapping");
  if (qs.includes("company_tax")) out.push("company_tax");
  if (qs.includes("policy")) out.push("policy");
  if (qs.includes("theme") || qs.includes("company")) out.push("theme");
  if (qs.includes("formats") || qs.includes("currency")) out.push("formats");
  // AM3: the company's own template, for an agent that can fill one — optional, skippable.
  if (takesTemplates(manifest?.templates)) out.push("template");
  out.push("selftest");
  return out;
}

/** The theme step asks the company name only when no company-details step already did. */
export function themeAsksCompany(manifest: Manifest | null | undefined): boolean {
  const qs = installQuestions(manifest);
  return qs.includes("company") && !qs.includes("company_tax");
}

/** Currency and materiality are the financial report's questions. */
export function asksCurrency(manifest: Manifest | null | undefined): boolean {
  return installQuestions(manifest).includes("currency");
}
