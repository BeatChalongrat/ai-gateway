"use client";

// /chat/agents/[slug] — what an agent needs, what it produces, what it will
// never do, the permissions it runs with and the capabilities it binds.

import Link from "next/link";
import { useEffect, useState } from "react";
import { FlaskConical, ShieldCheck } from "lucide-react";

import { AmError, amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import type { CatalogItem, InstallRow } from "../types";
import { AgentTile, BackLink, BTN_PRIMARY, BTN_SECONDARY, Card, CheckLine, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill } from "../ui";
import { runHref, workspaceHref } from "../workspace/context";
import { formatsLabel } from "./MarketPage";

const DENY_KEYS = new Set(["email_send", "accounting_write", "external_share", "payment", "computer_use", "schedule"]);
const CAP_KEYS = new Set([
  "spreadsheet.read.xlsx",
  "compute.python.decimal",
  "deliverable.report.docx",
  "deliverable.deck.pptx",
  "model.text",
  "deliverable.table.xlsx",
  "document.read.pdf",
  "document.extract.e2",
  "model.vision",
  "deliverable.pdf",
]);

export function AgentDetailPage({ slug }: { slug: string }) {
  return (
    <FeatureGate>
      <Detail slug={slug} />
    </FeatureGate>
  );
}

function Detail({ slug }: { slug: string }) {
  const t = useAmT();
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [installed, setInstalled] = useState<InstallRow | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    amApi.catalogItem(slug).then(
      (r) => alive && setItem(r),
      (err) => alive && setError(err),
    );
    amApi.installs().then(
      (r) => alive && setInstalled((r?.installs ?? []).find((i) => i.slug === slug) ?? null),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [slug, reload]);

  if (error) {
    const missing = error instanceof AmError && error.status === 404;
    return (
      <PageShell width="max-w-3xl">
        <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel title={missing ? t("detail.notFound") : undefined} message={errorMessage(error)} onRetry={missing ? undefined : () => setReload((n) => n + 1)} />
        </div>
      </PageShell>
    );
  }
  if (!item) return <PageSkeleton />;

  const m = item.manifest ?? {};
  const denied = m.permissions?.denied ?? [];
  const required = m.capabilities?.required ?? [];
  const optional = m.capabilities?.optional ?? [];
  const capLabel = (c: string) => (CAP_KEYS.has(c) ? t(`cap.${c}` as AmKey) : c);
  const formats = formatsLabel({ slug: item.slug, version: item.version, name: item.name, manifest: m });

  return (
    <PageShell width="max-w-3xl">
      <BackLink href="/chat/agents">{t("detail.back")}</BackLink>

      <div className="mt-5 flex items-start gap-4">
        <AgentTile icon={m.icon} size={48} />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">{item.name}</h1>
          <p className="mt-0.5 text-xs text-text-muted">
            {t("detail.version", { v: item.version })}
            {formats ? ` · ${formats}` : null}
          </p>
          {m.summary ? <p className="mt-2 text-sm leading-relaxed text-text-secondary">{m.summary}</p> : null}
        </div>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {installed ? (
          <Link href={workspaceHref(installed.id)} className={BTN_PRIMARY}>
            {t("market.open")}
          </Link>
        ) : (
          <Link href={`/chat/agents/install/${encodeURIComponent(item.slug)}`} className={BTN_PRIMARY} data-testid="am-detail-install">
            {t("market.install")}
          </Link>
        )}
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("detail.needs")}</h2>
          <ul className="mt-2.5 flex flex-col gap-2.5">
            {(m.inputs ?? []).map((i) => (
              <li key={i.role} className="text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-text-primary">{i.label}</span>
                  <Pill tone={i.required ? "warning" : "neutral"}>{i.required ? t("common.required") : t("common.optional")}</Pill>
                </div>
                {i.note ? <div className="mt-0.5 text-xs text-text-secondary">{i.note}</div> : null}
                {i.accept?.length ? <div className="mt-0.5 text-[11px] text-text-muted">{t("detail.accept", { types: i.accept.join(" ") })}</div> : null}
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("detail.produces")}</h2>
          <ul className="mt-2.5 flex flex-col gap-2">
            {(m.outputs ?? []).map((o) => (
              <li key={o.format} className="flex flex-wrap items-center gap-2 text-sm text-text-primary">
                <span>{o.label}</span>
                {o.default ? <Pill>{t("detail.defaultOutput")}</Pill> : null}
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <div className="flex items-center gap-2">
            <ShieldCheck width={16} height={16} className="text-status-success" aria-hidden />
            <h2 className="text-sm font-semibold text-text-primary">{t("detail.permissions")}</h2>
          </div>
          <ul className="mt-2.5 flex flex-col gap-1.5">
            <CheckLine>{t("detail.readOnly")}</CheckLine>
            {denied.map((d) => (
              <CheckLine key={d} ok={false}>
                {DENY_KEYS.has(d) ? t(`deny.${d}` as AmKey) : d}
              </CheckLine>
            ))}
          </ul>
          {m.cannot ? (
            <>
              <h3 className="mt-4 text-xs font-medium text-text-secondary">{t("detail.cannot")}</h3>
              <p className="mt-1 text-sm text-text-primary">{m.cannot}</p>
            </>
          ) : null}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("detail.capabilities")}</h2>
          <ul className="mt-2.5 flex flex-col gap-1.5">
            {required.map((c) => (
              <CheckLine key={c}>{capLabel(c)}</CheckLine>
            ))}
            {optional.map((c) => (
              <li key={c} className="flex items-start gap-2 text-sm text-text-muted">
                <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-current" aria-hidden />
                <span>{`${capLabel(c)} (${t("detail.capOptional")})`}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card className="mt-4">
        <div className="flex items-center gap-2">
          <FlaskConical width={16} height={16} className="text-text-secondary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">{t("detail.sample")}</h2>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-text-secondary">{t("detail.sampleBody")}</p>
        {installed?.self_test_run_id ? (
          <Link href={runHref(installed.self_test_run_id)} className={`${BTN_SECONDARY} mt-3`}>
            {t("detail.sampleOpen")}
          </Link>
        ) : null}
      </Card>
    </PageShell>
  );
}
