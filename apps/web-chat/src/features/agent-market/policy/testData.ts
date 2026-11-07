// Test data for the policy screens (not used by the app): the expense
// audit's sample table, expense_audit/agent.json install.defaults.policy,
// trimmed to three categories.

import type { ExpensePolicy, Manifest } from "../types";

export const SAMPLE: ExpensePolicy = {
  schema: "am2.expense_policy.v1",
  source: "package_sample",
  currency: "THB",
  receipt_required_above: "500.00",
  approval_threshold: "3000.00",
  categories: {
    meals: { label: "ค่าอาหาร", per_claim: "1500.00", per_person_month: "5000.00" },
    entertainment: { label: "ค่ารับรองลูกค้า", per_claim: "5000.00", needs_purpose: true },
    contractor: { label: "ค่าจ้างบุคคลภายนอก", per_claim: "20000.00", wht_rate: "3", wht_from: "1000.00" },
  },
  prohibited_merchants: ["คาราโอเกะ", "ผับ"],
  weekend_holiday: "flag",
  vat_claim_requires: "full_tax_invoice",
};

export const EXPENSE_MANIFEST: Manifest = {
  id: "sran.expense-audit",
  outputs: [{ format: "xlsx", label: "ผลตรวจ .xlsx", default: true }],
  install: { defaults: { formats: ["xlsx"], policy: SAMPLE }, questions: ["company", "policy", "theme", "formats"] },
};
