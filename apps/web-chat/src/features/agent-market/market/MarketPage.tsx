"use client";

// /chat/agents — the market (board 1): my installed agents first (when
// there are any), then the catalogue. Only real, installable agents get a
// button; the planned ones are shown as "เร็ว ๆ นี้" without one.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Search, Settings2 } from "lucide-react";

import { amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { formatDate } from "../format";
import type { CatalogAgent, InstallRow, SharedItem } from "../types";
import { AgentTile, BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL, cls, ErrorPanel, FeatureGate, PageShell, Pill, Tag } from "../ui";
import { useAgentMarket } from "../useAgentMarket";
import { workspaceHref } from "../workspace/context";
import { costLine } from "./trust";

type Category = "all" | "finance" | "documents" | "reports";
const CATEGORIES: { id: Category; key: AmKey }[] = [
  { id: "all", key: "market.cat.all" },
  { id: "finance", key: "market.cat.finance" },
  { id: "documents", key: "market.cat.documents" },
  { id: "reports", key: "market.cat.reports" },
];

/** The agents on the roadmap (PLAN): shown, labelled, never installable — until the real one (slug) is in the catalog. */
export const COMING_SOON: { id: string; icon: string; name: AmKey; desc: AmKey; cats: Category[]; slug?: string }[] = [
  { id: "invoice", icon: "invoice", name: "soon.invoice.name", desc: "soon.invoice.desc", cats: ["documents", "finance"],
    slug: "sran.invoice-processing" },
  { id: "bank", icon: "bank", name: "soon.bank.name", desc: "soon.bank.desc", cats: ["finance"] },
  { id: "expense", icon: "expense", name: "soon.expense.name", desc: "soon.expense.desc", cats: ["finance"],
    slug: "sran.expense-audit" },
  { id: "po", icon: "po", name: "soon.po.name", desc: "soon.po.desc", cats: ["finance", "documents"] },
  { id: "ap", icon: "ap", name: "soon.ap.name", desc: "soon.ap.desc", cats: ["finance"] },
  { id: "files", icon: "folder", name: "soon.files.name", desc: "soon.files.desc", cats: ["documents"] },
];

export function categoriesOf(a: CatalogAgent): Category[] {
  const out: Category[] = [];
  const c = a.manifest?.category;
  if (c === "finance" || c === "documents" || c === "reports") out.push(c);
  const outputs = (a.manifest?.outputs ?? []).map((o) => o.format);
  if (outputs.includes("docx") || outputs.includes("pptx")) out.push("reports");
  return out;
}

export function formatsLabel(a: CatalogAgent): string {
  return (a.manifest?.outputs ?? []).map((o) => `.${o.format}`).join(" + ");
}

export function MarketPage() {
  return (
    <FeatureGate>
      <Market />
    </FeatureGate>
  );
}

function Market() {
  const t = useAmT();
  const snap = useAgentMarket();
  const [agents, setAgents] = useState<CatalogAgent[] | null>(null);
  const [installs, setInstalls] = useState<InstallRow[]>([]);
  const [shared, setShared] = useState<SharedItem[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [cat, setCat] = useState<Category>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    setError(null);
    amApi.catalog().then(
      (r) => alive && setAgents(r?.agents ?? []),
      (err) => alive && setError(err),
    );
    amApi.installs().then(
      (r) => alive && setInstalls(r?.installs ?? []),
      () => {},
    );
    amApi.sharedWithMe().then(
      (r) => alive && setShared(r?.shared ?? []),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [reload]);

  const q = query.trim().toLowerCase();
  const matches = (text: string) => !q || text.toLowerCase().includes(q);
  const real = useMemo(
    () =>
      (agents ?? []).filter(
        (a) =>
          (cat === "all" || categoriesOf(a).includes(cat)) &&
          matches(`${a.name} ${a.manifest?.short_name ?? ""} ${a.manifest?.summary ?? ""}`),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agents, cat, q],
  );
  const shipped = new Set((agents ?? []).map((a) => a.slug));
  const soon = COMING_SOON.filter(
    (s) => !(s.slug && shipped.has(s.slug)) && (cat === "all" || s.cats.includes(cat)) && matches(`${t(s.name)} ${t(s.desc)}`),
  );
  const total = real.length + soon.length;

  return (
    <PageShell>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">{t("market.title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t("market.subtitle")}</p>
        </div>
        {snap.publisher ? (
          <Link href="/chat/agents/admin" className={BTN_SMALL} data-testid="am-admin-link">
            <Settings2 width={14} height={14} aria-hidden />
            <span>{t("admin.link")}</span>
          </Link>
        ) : null}
        <label className="relative block w-full sm:w-64">
          <span className="sr-only">{t("market.search")}</span>
          <Search width={15} height={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("market.searchPlaceholder")}
            className="focus-ring w-full rounded-lg border border-border bg-bg-surface py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none"
          />
        </label>
      </div>

      {installs.length ? <MyAgents installs={installs} /> : null}
      {shared.length ? <SharedWithMe items={shared} /> : null}

      <div className="mt-6 flex flex-wrap items-center gap-2" role="group" aria-label={t("market.categories")}>
        {CATEGORIES.map((c) => (
          <button
            key={c.id}
            type="button"
            aria-pressed={cat === c.id}
            onClick={() => setCat(c.id)}
            className={cls(
              "focus-ring rounded-full border px-3.5 py-1.5 text-sm transition-colors",
              cat === c.id ? "border-border-strong bg-bg-elevated text-text-primary" : "border-border text-text-secondary hover:bg-bg-elevated hover:text-text-primary",
            )}
          >
            {t(c.key)}
          </button>
        ))}
        <div className="flex-1" />
        {agents ? <span className="text-xs text-text-muted">{t("market.count", { n: String(total) })}</span> : null}
      </div>

      <div className="mt-4">
        {error ? <ErrorPanel title={t("market.loadFailed")} message={errorMessage(error)} onRetry={() => setReload((n) => n + 1)} /> : null}
        {!agents && !error ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
            <div className="skeleton h-48 rounded-xl sm:col-span-2" />
            <div className="skeleton h-48 rounded-xl" />
          </div>
        ) : null}
        {agents ? (
          total === 0 ? (
            <p className="py-10 text-center text-sm text-text-muted">{t("market.empty")}</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="am-market-grid">
              {real.map((a, i) => (
                <AgentCard key={a.slug} agent={a} featured={i === 0} />
              ))}
              {soon.map((s) => (
                <SoonCard key={s.id} icon={s.icon} name={t(s.name)} desc={t(s.desc)} />
              ))}
            </div>
          )
        ) : null}
      </div>
    </PageShell>
  );
}

function runTime(t: ReturnType<typeof useAmT>, seconds?: number): string | null {
  if (!seconds || seconds <= 0) return null;
  return seconds >= 60 ? t("market.runTimeMin", { n: String(Math.round(seconds / 60)) }) : t("market.runTimeSec", { n: String(seconds) });
}

function AgentCard({ agent, featured }: { agent: CatalogAgent; featured: boolean }) {
  const t = useAmT();
  const { pricing } = useAgentMarket();
  const m = agent.manifest ?? {};
  const installed = !!agent.install_id;
  const disabled = agent.status === "disabled";
  const cost = costLine(t, agent.sample, pricing);
  const time = cost ?? runTime(t, m.typical_run_seconds);
  const formats = formatsLabel(agent);
  return (
    <article
      className={cls(
        "flex flex-col gap-3 rounded-xl border bg-bg-surface p-5",
        featured ? "border-border-strong sm:col-span-2" : "border-border",
      )}
      data-testid="am-agent-card"
    >
      <div className="flex items-start gap-3.5">
        <AgentTile icon={m.icon} size={featured ? 42 : 38} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className={cls("font-semibold text-text-primary", featured ? "text-[17px]" : "text-[15px]")}>{agent.name}</h2>
            {featured && !disabled ? <Pill>{t("market.featured")}</Pill> : null}
            {disabled ? <Pill tone="warning">{t("rail.disabled")}</Pill> : null}
            {agent.status === "unpublished" ? <Pill>{t("market.unpublished")}</Pill> : null}
          </div>
          {m.summary ? <p className="mt-1 text-sm leading-relaxed text-text-secondary">{m.summary}</p> : null}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Tag>{t("market.tag.upload")}</Tag>
        {m.permissions?.default === "read_only" ? <Tag>{t("market.tag.readOnly")}</Tag> : null}
        {formats ? <Tag>{formats}</Tag> : null}
        {agent.sample?.status === "succeeded" ? <Tag tone="success">{t("market.hasSample")}</Tag> : null}
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
        {installed ? (
          <Link href={workspaceHref(agent.install_id!)} className={BTN_PRIMARY} data-testid="am-open-workspace">
            {t("market.open")}
          </Link>
        ) : disabled ? null : (
          <Link href={`/chat/agents/${encodeURIComponent(agent.slug)}`} className={BTN_PRIMARY} data-testid="am-install">
            {t("market.readFirst")}
          </Link>
        )}
        {installed || disabled ? (
          <Link href={`/chat/agents/${encodeURIComponent(agent.slug)}`} className={BTN_SECONDARY}>
            {t("market.details")}
          </Link>
        ) : null}
        <div className="flex-1" />
        {time ? <span className="text-xs text-text-muted">{time}</span> : null}
      </div>
    </article>
  );
}

function SoonCard({ icon, name, desc }: { icon: string; name: string; desc: string }) {
  const t = useAmT();
  return (
    <article className="flex flex-col gap-2.5 rounded-xl border border-border-subtle bg-bg-base p-5" data-testid="am-soon-card">
      <div className="flex items-center justify-between gap-2">
        <AgentTile icon={icon} size={36} quiet />
        <Pill>{t("market.soon")}</Pill>
      </div>
      <h2 className="text-[15px] font-semibold text-text-secondary">{name}</h2>
      <p className="text-sm leading-relaxed text-text-muted">{desc}</p>
    </article>
  );
}

function MyAgents({ installs }: { installs: InstallRow[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  return (
    <section className="mt-6" aria-labelledby="am-mine">
      <h2 id="am-mine" className="text-sm font-semibold text-text-primary">
        {t("market.mine")}
      </h2>
      <ul className="mt-2 grid gap-2 sm:grid-cols-2">
        {installs.map((i) => (
          <li key={i.id}>
            <Link
              href={workspaceHref(i.id)}
              className="focus-ring flex items-center gap-3 rounded-xl border border-border bg-bg-surface px-3.5 py-3 transition-colors hover:border-border-strong"
              data-testid="am-my-agent"
            >
              <AgentTile icon={i.icon} size={32} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-text-primary">{i.title}</span>
                <span className="block truncate text-xs text-text-muted">
                  {i.last_run ? t("market.mine.lastRun", { when: formatDate(i.last_run.created_at, locale) }) : t("market.mine.never")}
                </span>
              </span>
              {i.attention ? <Pill tone="warning">{t("rail.attention")}</Pill> : null}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SharedWithMe({ items }: { items: SharedItem[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  return (
    <section className="mt-6" aria-labelledby="am-shared">
      <h2 id="am-shared" className="text-sm font-semibold text-text-primary">
        {t("market.shared")}
      </h2>
      <ul className="mt-2 grid gap-2 sm:grid-cols-2" data-testid="am-shared-list">
        {items.map((s) => (
          <li key={s.id}>
            <Link
              href={`/chat/agents/shared/${s.id}`}
              className="focus-ring flex items-center gap-3 rounded-xl border border-border bg-bg-surface px-3.5 py-3 transition-colors hover:border-border-strong"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-text-primary">
                  {s.period ? `${s.agent} · ${s.period}` : s.agent}
                </span>
                <span className="block truncate text-xs text-text-muted">
                  {t("market.sharedBy", { who: s.owner, when: formatDate(s.created_at, locale) })}
                </span>
              </span>
              {s.can_review ? <Pill>{t("share.canReviewTag")}</Pill> : null}
              {!s.last_opened_at ? <Pill tone="warning">{t("market.sharedNew")}</Pill> : null}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
