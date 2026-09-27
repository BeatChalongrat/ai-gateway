"use client";

// AM3 — the workspace's budget line. Quiet while there is room; a calm note
// when less than a tenth of any monthly limit is left; a clear one when a
// limit is used up, naming whom to ask. A run that reaches a limit is never
// failed: it waits, and its notice in the thread offers "ลองต่อ".

import { useEffect, useState } from "react";
import { Wallet } from "lucide-react";

import { amApi } from "../api";
import { useAmT, type AmT } from "../i18n";
import { formatCredits } from "../format";
import type { BudgetState, Contact } from "../types";
import { cls } from "../ui";

export function contactLabel(c: Contact | null | undefined, t: AmT): string {
  if (!c) return t("budget.contactFallback");
  if (c.name && c.email) return `${c.name} (${c.email})`;
  return c.name || c.email || t("budget.contactFallback");
}

/** The limit with the least room: which one, what is left, and its size. */
export function tightest(b: BudgetState): { which: "user" | "department" | "install"; left: number; limit: number } | null {
  const all: { which: "user" | "department" | "install"; left: number; limit: number }[] = [];
  if (b.user_limit > 0) all.push({ which: "user", left: b.user_limit - b.user_spent, limit: b.user_limit });
  if (b.department_limit !== null && b.department_limit !== undefined && b.department_limit > 0)
    all.push({ which: "department", left: b.department_limit - b.department_spent, limit: b.department_limit });
  if (b.install_limit > 0) all.push({ which: "install", left: b.install_limit - b.install_spent, limit: b.install_limit });
  if (!all.length) return null;
  return all.reduce((a, x) => (x.left / x.limit < a.left / a.limit ? x : a));
}

export function budgetWhich(which: string, b: BudgetState, t: AmT): string {
  if (which === "department") return t("budget.whichDepartment", { name: b.department ?? "" });
  if (which === "install") return t("budget.whichInstall");
  return t("budget.whichUser");
}

export function BudgetBanner({ installId, refreshKey }: { installId: string; refreshKey?: string }) {
  const t = useAmT();
  const [b, setB] = useState<BudgetState | null>(null);
  useEffect(() => {
    let alive = true;
    amApi.budget(installId).then(
      (r) => alive && setB(r?.budget ?? null),
      () => {
        /* no banner when the budget cannot be read */
      },
    );
    return () => {
      alive = false;
    };
  }, [installId, refreshKey]);
  if (!b) return null;
  const tight = tightest(b);
  const out = !!b.exhausted;
  if (!out && (!tight || tight.left / tight.limit >= 0.1)) return null;
  const which = out ? b.exhausted : tight!.which;
  const text = out
    ? t("budget.out", { which: budgetWhich(which, b, t), who: contactLabel(b.contact, t) })
    : t("budget.low", { which: budgetWhich(which, b, t), left: formatCredits(Math.max(0, tight!.left)), who: contactLabel(b.contact, t) });
  return (
    <div
      role={out ? "alert" : "status"}
      className={cls(
        "mx-auto mb-2 flex w-full max-w-chat items-start gap-2 rounded-lg border px-3 py-2 text-xs",
        out ? "border-status-warning/50 bg-status-warning/10 text-text-primary" : "border-border bg-bg-surface text-text-secondary",
      )}
      data-testid="am-budget-banner"
      data-state={out ? "out" : "low"}
    >
      <Wallet width={14} height={14} className="mt-px shrink-0" aria-hidden />
      <span>{text}</span>
    </div>
  );
}
