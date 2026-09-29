"use client";

// /chat/agents/[slug]/datasheet — the per-agent data sheet (AM3), generated
// from the manifest and what the runner binds: what it reads, what it
// produces, which models see which part of the data and whether each runs in
// the organisation, what no model sees, where files are kept and for how
// long, how it is isolated, its permissions and what it never does.
// Procurement and the person clicking install read the same page; it prints
// without the app around it.

import { Printer } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { formatDateTime } from "../format";
import type { DatasheetRetention } from "../types";
import { BackLink, CheckLine, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill } from "../ui";
import { ModelList, ScopeLine, useDatasheet } from "./trust";

const DENY_KEYS = new Set(["email_send", "accounting_write", "external_share", "payment", "computer_use", "schedule"]);

const PRINT_CSS = `@media print {
  body * { visibility: hidden !important; }
  [data-testid="am-datasheet"], [data-testid="am-datasheet"] * { visibility: visible !important; }
  [data-testid="am-datasheet"] { position: absolute; left: 0; top: 0; width: 100%; padding: 16px; }
}`;

export function DatasheetPage({ slug }: { slug: string }) {
  return (
    <FeatureGate>
      <Sheet slug={slug} />
    </FeatureGate>
  );
}

export function keepText(r: DatasheetRetention, t: (k: AmKey, v?: Record<string, string>) => string): string {
  switch (r.unit) {
    case "hours":
      return t("ds.keepHours", { n: String(r.keep) });
    case "days":
      return t("ds.keepDays", { n: String(r.keep) });
    case "years":
      return t("ds.keepYears", { n: String(r.keep) });
    case "while_installed":
      return t("ds.keepWhileInstalled");
    case "account_deletion":
      return t("ds.keepOrgRecord");
  }
  return String(r.keep);
}

function Sheet({ slug }: { slug: string }) {
  const t = useAmT();
  const locale = useAmLocale();
  const { sheet, error } = useDatasheet(slug);
  if (error && !sheet) {
    return (
      <PageShell width="max-w-3xl">
        <BackLink href={`/chat/agents/${encodeURIComponent(slug)}`}>{t("common.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel message={errorMessage(error)} />
        </div>
      </PageShell>
    );
  }
  if (!sheet) return <PageSkeleton />;
  const denied = (sheet.permissions?.denied ?? []).filter((d) => DENY_KEYS.has(d));
  const backend = sheet.storage?.backend === "s3" ? t("ds.storageS3") : t("ds.storageLocal");

  return (
    <PageShell width="max-w-3xl">
      {/* Print the sheet alone: no sidebar, no app chrome. */}
      <style>{PRINT_CSS}</style>
      <div className="print:hidden">
        <BackLink href={`/chat/agents/${encodeURIComponent(slug)}`}>{t("common.back")}</BackLink>
      </div>
      <article className="mt-4 flex flex-col gap-5 text-sm text-text-primary" data-testid="am-datasheet">
        <header className="flex flex-wrap items-start gap-3 border-b border-border-subtle pb-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium tracking-wide text-text-muted">{t("ds.kicker")}</p>
            <h1 className="mt-1 text-xl font-semibold tracking-tight">{sheet.name}</h1>
            <p className="mt-0.5 text-xs text-text-muted">{`${sheet.slug} · ${t("detail.version", { v: sheet.version })}`}</p>
            <ScopeLine scope={sheet.scope} jurisdiction={sheet.jurisdiction} className="mt-0.5" />
            {sheet.summary ? <p className="mt-2 text-sm leading-relaxed text-text-secondary">{sheet.summary}</p> : null}
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={() => window.print()} className="print:hidden" data-testid="am-ds-print">
            <Printer width={14} height={14} aria-hidden />
            <span>{t("ds.print")}</span>
          </Button>
        </header>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.reads")}</h2>
          <ul className="mt-2 flex flex-col gap-2">
            {(sheet.reads ?? []).map((r) => (
              <li key={r.role}>
                <div className="flex flex-wrap items-center gap-2">
                  <span>{r.label}</span>
                  <Pill tone={r.required ? "warning" : "neutral"}>{r.required ? t("common.required") : t("common.optional")}</Pill>
                </div>
                {r.note ? <div className="text-xs text-text-secondary">{r.note}</div> : null}
                {r.accept?.length ? <div className="text-[11px] text-text-muted">{t("detail.accept", { types: r.accept.join(" ") })}</div> : null}
              </li>
            ))}
          </ul>
          {sheet.limits?.max_file_mb || sheet.limits?.max_files ? (
            <p className="mt-2 text-xs text-text-muted">
              {t("ds.limits", { mb: String(sheet.limits?.max_file_mb ?? "—"), files: String(sheet.limits?.max_files ?? "—") })}
            </p>
          ) : null}
        </section>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.produces")}</h2>
          <ul className="mt-2 flex flex-col gap-1">
            {(sheet.produces ?? []).map((p) => (
              <li key={p.format} className="text-sm">{`${p.label} (.${p.format})`}</li>
            ))}
          </ul>
        </section>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.models")}</h2>
          <p className="mt-0.5 text-xs text-text-muted">{t("ds.modelsBody")}</p>
          <div className="mt-2">
            <ModelList models={sheet.models ?? []} noModel={sheet.no_model ?? []} />
          </div>
        </section>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.storage")}</h2>
          <p className="mt-1 text-sm text-text-secondary">{t("ds.storageLine", { where: backend, prefix: sheet.storage?.prefix ?? "agent-market/" })}</p>
          <table className="mt-2 w-full border-collapse text-sm" data-testid="am-ds-retention">
            <thead>
              <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
                <th className="py-1.5 pr-3 font-medium">{t("ds.what")}</th>
                <th className="py-1.5 font-medium">{t("ds.keep")}</th>
              </tr>
            </thead>
            <tbody>
              {(sheet.retention ?? []).map((r) => (
                <tr key={r.what} className="border-b border-border-subtle align-top">
                  <td className="py-1.5 pr-3 text-text-secondary">{r.what}</td>
                  <td className="py-1.5">{keepText(r, t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sheet.notes?.not_archive ? (
            <p className="mt-2 text-xs text-text-secondary" data-testid="am-ds-not-archive">
              {sheet.notes.not_archive}
            </p>
          ) : null}
        </section>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.isolation")}</h2>
          <ul className="mt-2 flex flex-col gap-1.5">
            {(sheet.isolation ?? []).map((x) => (
              <CheckLine key={x}>{x}</CheckLine>
            ))}
          </ul>
        </section>

        <section>
          <h2 className="text-sm font-semibold">{t("ds.permissions")}</h2>
          <ul className="mt-2 flex flex-col gap-1.5">
            <CheckLine>{t("detail.readOnly")}</CheckLine>
            {denied.map((d) => (
              <CheckLine key={d} ok={false}>
                {t(`deny.${d}` as AmKey)}
              </CheckLine>
            ))}
            {(sheet.never ?? []).map((n) => (
              <CheckLine key={n} ok={false}>
                {n}
              </CheckLine>
            ))}
          </ul>
          {sheet.cannot ? <p className="mt-2 text-sm">{sheet.cannot}</p> : null}
        </section>

        <footer className="border-t border-border-subtle pt-3 text-[11px] text-text-muted">
          {t("ds.generated", {
            when: formatDateTime(sheet.generated_at, locale),
            runner: sheet.runner?.healthy ? t("ds.runnerOk", { v: sheet.runner.version ?? "" }) : t("ds.runnerDown"),
          })}
        </footer>
      </article>
    </PageShell>
  );
}
