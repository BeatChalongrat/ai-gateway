"use client";

import { useT } from "@/lib/i18n";
import type { OfficeQualityReport } from "@/lib/types";

export function OfficeQualityNotice({ report }: { report?: OfficeQualityReport }) {
  const t = useT();
  if (!report) return null;
  return (
    <div className="mt-3 rounded-lg border border-border-subtle bg-bg-base p-3 text-xs text-text-secondary" role="status">
      <p>{report.status === "checked"
        ? t("plan.office.rendered", { n: String(report.rendered_pages ?? 0) })
        : t("plan.office.partial")}</p>
      {!!report.formulas && <p>{t(report.recalculated ? "plan.office.recalculated" : "plan.office.notRecalculated", { n: String(report.formulas) })}</p>}
      {!!report.repair_attempts && <p>{t(report.repaired ? "editor.repaired" : "editor.retained", { n: String(report.repair_attempts) })}</p>}
      <p>{t("plan.office.review")}</p>
      {report.warnings?.length ? <details className="mt-1"><summary>{t("plan.office.details")}</summary>
        <ul className="list-disc pl-4">{report.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      </details> : null}
    </div>
  );
}
