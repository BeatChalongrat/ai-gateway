"use client";

// /chat/agents/mine — "ดูทั้งหมด" from the rail (AM3): every installed agent,
// in the rail's own order (pinned, then recently used — never alphabetical),
// a filter box, the pin toggle and what waits for an answer.

import Link from "next/link";
import { useMemo, useState } from "react";
import { Pin, PinOff, Search } from "lucide-react";

import { useAmLocale, useAmT } from "../i18n";
import { formatDateTime } from "../format";
import { railOrder } from "../rail";
import type { RailInstall } from "../types";
import { AgentTile, BackLink, cls, FeatureGate, PageShell, Pill } from "../ui";
import { workspaceHref } from "../workspace/context";
import { togglePin, useRail } from "../useRail";

export function MinePage() {
  return (
    <FeatureGate>
      <Mine />
    </FeatureGate>
  );
}

function Mine() {
  const t = useAmT();
  const locale = useAmLocale();
  const rail = useRail(true);
  const [q, setQ] = useState("");
  const ordered = useMemo(() => railOrder(rail.installs, rail.installs.length).shown, [rail.installs]);
  const needle = q.trim().toLocaleLowerCase("th");
  const rows = needle ? ordered.filter((r) => `${r.title} ${r.slug}`.toLocaleLowerCase("th").includes(needle)) : ordered;

  const pin = (row: RailInstall) => {
    const was = !!row.pinned_at;
    rail.patch(row.id, { pinned_at: was ? null : new Date().toISOString() });
    togglePin(row.id, was).then(
      (at) => rail.patch(row.id, { pinned_at: at }),
      () => rail.patch(row.id, { pinned_at: row.pinned_at }),
    );
  };

  return (
    <PageShell width="max-w-3xl">
      <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">{t("mine.title", { n: String(rail.installs.length) })}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t("mine.body")}</p>
        </div>
        <label className="relative block w-full sm:w-64">
          <span className="sr-only">{t("mine.filter")}</span>
          <Search width={15} height={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" aria-hidden />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("mine.filter")}
            className="focus-ring w-full rounded-lg border border-border bg-bg-surface py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted"
            data-testid="am-mine-filter"
          />
        </label>
      </div>
      {rail.waitingTotal > 0 ? (
        <p className="mt-4 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-text-primary" role="status">
          {t("mine.waiting", { n: String(rail.waitingTotal) })}
        </p>
      ) : null}
      {!rail.loaded ? (
        <div className="mt-6 grid gap-2" aria-busy="true">
          <div className="skeleton h-14 rounded-xl" />
          <div className="skeleton h-14 rounded-xl" />
        </div>
      ) : rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-text-muted">{rail.installs.length ? t("mine.noMatch") : t("mine.none")}</p>
      ) : (
        <ul className="mt-5 flex flex-col gap-2" data-testid="am-mine-list">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center gap-3 rounded-xl border border-border bg-bg-surface px-3.5 py-3" data-testid="am-mine-row">
              <Link href={workspaceHref(r.id)} className="focus-ring flex min-w-0 flex-1 items-center gap-3 rounded-lg">
                <AgentTile icon={r.icon} size={32} quiet={r.disabled} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-text-primary">{r.title}</span>
                  <span className="block truncate text-xs text-text-muted">
                    {r.last_used_at ? t("mine.lastUsed", { when: formatDateTime(r.last_used_at, locale) }) : t("market.mine.never")}
                  </span>
                </span>
                {r.disabled ? <Pill>{t("rail.disabled")}</Pill> : null}
                {r.waiting > 0 ? <Pill tone="warning">{t("mine.rowWaiting", { n: String(r.waiting) })}</Pill> : null}
              </Link>
              <button
                type="button"
                onClick={() => pin(r)}
                aria-pressed={!!r.pinned_at}
                aria-label={r.pinned_at ? t("rail.unpin", { name: r.title }) : t("rail.pin", { name: r.title })}
                className={cls(
                  "focus-ring inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-bg-elevated",
                  r.pinned_at ? "text-text-primary" : "text-text-muted",
                )}
                data-testid="am-mine-pin"
              >
                {r.pinned_at ? <PinOff width={15} height={15} aria-hidden /> : <Pin width={15} height={15} aria-hidden />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </PageShell>
  );
}
