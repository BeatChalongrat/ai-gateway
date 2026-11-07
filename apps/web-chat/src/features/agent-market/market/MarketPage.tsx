"use client";

// /chat/agents — the market (board 1): my installed agents first (when
// there are any), then the catalogue. Only real, installable agents get a
// button; the planned ones are shown as "เร็ว ๆ นี้" without one.

import Link from "next/link";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";

import { ButtonLink } from "@/components/ui/Button";

import { amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { formatDate } from "../format";
import type { CatalogAgent, InstallRow, SharedItem } from "../types";
import { AgentTile, cls, ErrorPanel, FeatureGate, PageShell, Pill } from "../ui";
import { useAgentMarket } from "../useAgentMarket";
import { workspaceHref } from "../workspace/context";

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
  { id: "bank", icon: "bank", name: "soon.bank.name", desc: "soon.bank.desc", cats: ["finance"],
    slug: "sran.bank-reconciliation" },
  { id: "expense", icon: "expense", name: "soon.expense.name", desc: "soon.expense.desc", cats: ["finance"],
    slug: "sran.expense-audit" },
  { id: "po", icon: "po", name: "soon.po.name", desc: "soon.po.desc", cats: ["finance", "documents"],
    slug: "sran.invoice-po-matching" },
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
          <div className="grid auto-rows-fr gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
            <div className="skeleton h-64 rounded-xl" />
            <div className="skeleton h-64 rounded-xl" />
            <div className="skeleton h-64 rounded-xl" />
          </div>
        ) : null}
        {agents ? (
          total === 0 ? (
            <p className="py-10 text-center text-sm text-text-muted">{t("market.empty")}</p>
          ) : (
            // AM4: one grid of equal cards — every row the same height, every
            // card's parts in the same place, the ones that work first.
            <div className="grid auto-rows-fr gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="am-market-grid">
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

/** AM10 — what a person deciding on an agent needs: what to feed it (the
 *  required inputs, then the optional ones) and what comes back (the outputs
 *  it makes by default). Price and time are not here: cost is the admin's,
 *  on the spend page, and the time is said when a round starts. */
export function feedLine(a: CatalogAgent): { required: string; optional: string } {
  const inputs = a.manifest?.inputs ?? [];
  const label = (i: { label?: string; accept?: string[] }) => (i.label ?? "").trim();
  return {
    required: inputs.filter((i) => i.required && label(i)).map(label).join(" · "),
    optional: inputs.filter((i) => !i.required && label(i)).map(label).join(" · "),
  };
}

export function getLine(a: CatalogAgent): string {
  const outputs = a.manifest?.outputs ?? [];
  const chosen = outputs.filter((o) => o.default);
  return (chosen.length ? chosen : outputs).map((o) => (o.label ?? `.${o.format}`).trim()).filter(Boolean).join(" · ");
}

/** A description clamped to three lines, with "ดูเพิ่ม" when it is longer. */
export function Clamp({ text, lines = 3 }: { text: string; lines?: 3 | 4 }) {
  const t = useAmT();
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOverflows(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return (
    <div className="min-h-[4.5rem]">
      <p
        ref={ref}
        className={cls("text-sm leading-relaxed text-text-secondary", open ? "" : lines === 4 ? "line-clamp-4" : "line-clamp-3")}
        data-testid="am-card-desc"
      >
        {text}
      </p>
      {overflows || open ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="focus-ring mt-0.5 rounded text-xs text-text-secondary underline underline-offset-2 hover:text-text-primary"
          data-testid="am-card-more"
        >
          {open ? t("market.less") : t("market.more")}
        </button>
      ) : null}
    </div>
  );
}

/** The frame every catalog card shares: the same parts in the same places. */
function CardFrame({
  soon = false,
  head,
  desc,
  meta,
  actions,
  testId,
}: {
  soon?: boolean;
  head: React.ReactNode;
  desc: React.ReactNode;
  meta: React.ReactNode;
  actions: React.ReactNode;
  testId: string;
}) {
  return (
    <article
      className={cls(
        "flex h-full flex-col rounded-xl border p-5",
        soon ? "border-dashed border-border-subtle bg-bg-base opacity-60" : "border-border bg-bg-surface shadow-sm",
      )}
      data-testid={testId}
    >
      <div className="flex min-h-[2.75rem] items-start gap-3">{head}</div>
      <div className="mt-3">{desc}</div>
      {/* what to feed it, what comes back, and the actions: one block pinned
          to the bottom, so each sits at the same height on every card of a
          row, however long the name or the description above it */}
      <div className="mt-auto pt-3" data-testid="am-card-bottom">
        <div className="min-h-[2.5rem] text-xs text-text-primary" data-testid="am-card-meta">
          {meta}
        </div>
        <div className="mt-3 flex min-h-[2.25rem] flex-wrap items-center gap-2 border-t border-border-subtle pt-3" data-testid="am-card-actions">
          {actions}
        </div>
      </div>
    </article>
  );
}

function AgentCard({ agent, featured }: { agent: CatalogAgent; featured: boolean }) {
  const t = useAmT();
  const m = agent.manifest ?? {};
  const installed = !!agent.install_id;
  const disabled = agent.status === "disabled";
  const feed = feedLine(agent);
  const get = getLine(agent);
  return (
    <CardFrame
      testId="am-agent-card"
      head={
        <>
          <AgentTile icon={m.icon} look={m} size={38} />
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 text-[15px] font-semibold leading-snug text-text-primary">{agent.name}</h2>
            {disabled || agent.status === "unpublished" ? (
              <div className="mt-1 flex flex-wrap gap-1.5">
                {disabled ? <Pill tone="warning">{t("rail.disabled")}</Pill> : null}
                {agent.status === "unpublished" ? <Pill>{t("market.unpublished")}</Pill> : null}
              </div>
            ) : null}
          </div>
        </>
      }
      desc={m.summary ? <Clamp text={m.summary} /> : <div className="min-h-[4.5rem]" />}
      meta={
        <>
          <span className="block truncate" data-testid="am-card-feed">
            <span className="text-text-secondary">{t("card.feed")}</span>
            {` ${feed.required}`}
            {feed.optional ? <span className="text-text-muted">{` · ${t("card.optional", { items: feed.optional })}`}</span> : null}
          </span>
          <span className="block truncate" data-testid="am-card-get">
            <span className="text-text-secondary">{t("card.get")}</span>
            {` ${get}`}
          </span>
        </>
      }
      actions={
        <>
          {installed ? (
            <ButtonLink href={workspaceHref(agent.install_id!)} data-testid="am-open-workspace">
              {t("market.open")}
            </ButtonLink>
          ) : disabled ? null : (
            <ButtonLink href={`/chat/agents/${encodeURIComponent(agent.slug)}`} data-testid="am-install">
              {t("market.readFirst")}
            </ButtonLink>
          )}
          {installed || disabled ? (
            <ButtonLink href={`/chat/agents/${encodeURIComponent(agent.slug)}`} variant="secondary">
              {t("market.details")}
            </ButtonLink>
          ) : null}
        </>
      }
    />
  );
}

function SoonCard({ icon, name, desc }: { icon: string; name: string; desc: string }) {
  const t = useAmT();
  return (
    <CardFrame
      soon
      testId="am-soon-card"
      head={
        <>
          <AgentTile icon={icon} size={38} quiet />
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 text-[15px] font-semibold leading-snug text-text-secondary">{name}</h2>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Pill>{t("market.soon")}</Pill>
            </div>
          </div>
        </>
      }
      desc={<Clamp text={desc} />}
      meta=""
      actions={<span className="text-xs text-text-muted">{t("market.soonNote")}</span>}
    />
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
              <AgentTile icon={i.icon} look={i} size={32} />
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
