"use client";

// /chat/agents/i/[installId]/dashboard — how one installed agent has been
// doing for its owner (AM2 phase G, GET /installs/{id}/dashboard?days=N):
// runs and the share done without a question, what is waiting now and for
// how long, the time runs queue / work / wait on a person, the questions it
// asks, the exceptions it leaves and what people decided, why runs failed,
// how sure its readings were, what it spent and which models saw the work
// (in the organisation or not). Plain CSS bars, no chart library.
//
// AM6 — a round that delivered its draft while the agent's gate did not pass
// is counted apart from the rounds that failed, and what the platform paid
// for rounds that delivered nothing is shown as charged to nobody.

import Link from "next/link";
import { useEffect, useState } from "react";

import { Spinner } from "@/components/ui/Spinner";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { DASH, formatCredits, formatDate, formatDateTime, formatInt } from "../format";
import { COMPLETED_OPEN, NOT_TIED, outcomeKey } from "../outcome";
import type { AgentDashboard, InstallDetail } from "../types";
import { BackLink, Card, cls, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill } from "../ui";
import { reasonLabelKey } from "../exceptions/exceptions";
import { settingsHref, workspaceHref } from "../workspace/context";
import { bars, CONFIDENCE_ORDER, formatRate, formatSeconds, isEmpty, RANGES, recordBars, TIER_ORDER, type Bar } from "./dashboard";

export function DashboardPage({ installId }: { installId: string }) {
  return (
    <FeatureGate>
      <Dashboard installId={installId} />
    </FeatureGate>
  );
}

type TimeKey = keyof AgentDashboard["time"];
const TIME_ROWS: [AmKey, TimeKey, TimeKey][] = [
  ["dash.time.queue", "queue_p50_s", "queue_p90_s"],
  ["dash.time.work", "work_p50_s", "work_p90_s"],
  ["dash.time.human", "human_wait_p50_s", "human_wait_p90_s"],
];

const STATUS_KEYS: Record<string, AmKey> = {
  succeeded: "progress.title.succeeded",
  completed: "progress.title.succeeded",
  failed: "progress.title.failed",
  cancelled: "progress.title.cancelled",
  awaiting_input: "progress.title.awaiting_input",
  running: "progress.title.running",
  queued: "progress.title.queued",
};

/** AM6: the one gate every open round of the window ended on (its code), or
 *  "" when they ended on more than one: a count of them all is then named
 *  by the agent's gate, not by one of them. */
function openReason(d: AgentDashboard): string {
  const keys = (d.open ?? []).filter((o) => o.count > 0).map((o) => o.key);
  return keys.length === 1 ? keys[0]! : "";
}

/** The words for rounds that finished open, where they stand in a line of counts. */
function openCountKey(d: AgentDashboard): AmKey {
  return openReason(d) === NOT_TIED ? "outcome.open.notTiedCount" : "outcome.open.gateCount";
}

function Dashboard({ installId }: { installId: string }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [days, setDays] = useState<number>(30);
  const [detail, setDetail] = useState<InstallDetail | null>(null);
  const [data, setData] = useState<AgentDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    amApi.getInstall(installId).then(
      (d) => alive && setDetail(d),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    amApi.dashboard(installId, days).then(
      (d) => {
        if (!alive) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (err) => {
        if (!alive) return;
        setError(err);
        setLoading(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [installId, days, reload]);

  if (error && !data) {
    const missing = error instanceof AmError && (error.status === 404 || error.status === 400);
    return (
      <PageShell width="max-w-3xl">
        <BackLink href={missing ? "/chat/agents" : workspaceHref(installId)}>{missing ? t("ws.backToMarket") : t("settings.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel
            title={missing ? t("ws.notFound.title") : t("dash.loadFailed")}
            message={errorMessage(error)}
            onRetry={missing ? undefined : () => setReload((n) => n + 1)}
          />
        </div>
      </PageShell>
    );
  }
  if (!data) return <PageSkeleton />;

  const title = detail?.install.title ?? "";
  // AM6: a server that counts rounds finished open sends failures without them
  const counted = typeof data.runs.completed_open === "number";
  const failed = counted ? data.runs.failed ?? 0 : data.runs.by_status?.failed ?? 0;
  const notCharged = data.spend.cost_not_charged ?? 0;
  const perDoc = data.spend.per_document ?? data.spend.per_succeeded_run;
  const reasonLabel = (key: string) => {
    const k = reasonLabelKey(key);
    return k ? t(k) : key;
  };

  return (
    <PageShell width="max-w-5xl">
      <BackLink href={workspaceHref(installId)}>{t("settings.back")}</BackLink>
      <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">{t("dash.title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">
            {[title, data.version ? t("dash.version", { v: data.version }) : null, t("dash.since", { when: formatDate(data.since, locale) })]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <div role="group" aria-label={t("dash.range")} className="flex items-center gap-1 rounded-lg border border-border p-0.5" data-testid="am-dash-range">
          {RANGES.map((n) => (
            <button
              key={n}
              type="button"
              aria-pressed={days === n}
              onClick={() => setDays(n)}
              className={cls(
                "focus-ring rounded-md px-2.5 py-1 text-xs tabular-nums transition-colors",
                days === n ? "bg-bg-elevated font-medium text-text-primary" : "text-text-secondary hover:text-text-primary",
              )}
            >
              {t("dash.days", { n: String(n) })}
            </button>
          ))}
        </div>
        {loading ? <Spinner size={14} /> : null}
      </div>

      {isEmpty(data) ? (
        <Card className="mt-6">
          <p className="text-sm text-text-primary" data-testid="am-dash-empty">
            {t("dash.empty", { n: String(data.days) })}
          </p>
          <p className="mt-1 text-xs text-text-muted">{t("dash.emptyHint")}</p>
          <SelfTestLine d={data} />
        </Card>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-5" data-testid="am-dash-kpis">
            <Tile
              label={t("dash.kpi.runs")}
              value={formatInt(data.runs.total)}
              sub={
                counted
                  ? t("dash.kpi.runsSubOpen", {
                      ok: formatInt(data.runs.succeeded),
                      state: t(openCountKey(data)),
                      open: formatInt(data.runs.completed_open),
                      failed: formatInt(failed),
                    })
                  : t("dash.kpi.runsSub", { ok: formatInt(data.runs.succeeded), failed: formatInt(failed) })
              }
            />
            <Tile
              label={t("dash.kpi.firstPass")}
              value={formatRate(data.runs.first_pass_rate)}
              sub={data.runs.succeeded ? t("dash.kpi.firstPassSub", { n: formatInt(data.runs.first_pass), total: formatInt(data.runs.succeeded) }) : t("dash.none")}
            />
            <Tile
              label={t("dash.kpi.waiting")}
              value={formatInt(data.waiting.runs)}
              sub={data.waiting.runs ? t("dash.oldest", { age: formatSeconds(data.waiting.oldest_run_s, t) }) : t("dash.nothingWaiting")}
              tone={data.waiting.runs ? "warning" : null}
            />
            <Tile
              label={t("dash.kpi.exceptions")}
              value={formatInt(data.waiting.open_exceptions)}
              sub={data.waiting.open_exceptions ? t("dash.oldest", { age: formatSeconds(data.waiting.oldest_exception_s, t) }) : t("dash.nothingWaiting")}
              tone={data.waiting.open_exceptions ? "warning" : null}
            />
            <Tile
              label={data.spend.per_document !== null ? t("dash.kpi.perDoc") : t("dash.kpi.perRun")}
              value={perDoc === null ? DASH : formatCredits(perDoc)}
              sub={t("dash.kpi.spendSub", { credits: formatCredits(data.spend.credits), budget: formatCredits(data.spend.budget_credits) })}
            />
          </div>
          {notCharged > 0 ? (
            <p className="mt-2 text-xs text-text-muted" data-testid="am-dash-not-charged">
              {t("dash.costNotCharged", { credits: formatCredits(notCharged) })}
            </p>
          ) : null}

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.time")}</h2>
              <table className="mt-2 w-full text-left text-xs" data-testid="am-dash-time">
                <thead className="text-text-muted">
                  <tr>
                    <th scope="col" className="py-1 font-normal" />
                    <th scope="col" className="py-1 font-normal">{t("dash.p50")}</th>
                    <th scope="col" className="py-1 font-normal">{t("dash.p90")}</th>
                  </tr>
                </thead>
                <tbody className="text-text-primary">
                  {TIME_ROWS.map(([k, p50, p90]) => (
                    <tr key={k} className="border-t border-border-subtle">
                      <th scope="row" className="py-1.5 pr-3 font-normal text-text-secondary">
                        {t(k)}
                      </th>
                      <td className="py-1.5 pr-3 tabular-nums">{formatSeconds(data.time[p50], t)}</td>
                      <td className="py-1.5 tabular-nums">{formatSeconds(data.time[p90], t)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <StatusLine d={data} />
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.exceptions")}</h2>
              <p className="mt-1 text-xs text-text-secondary">
                {t("dash.exceptionsLine", {
                  open: formatInt(data.exceptions.open),
                  resolved: formatInt(data.exceptions.resolved),
                  noted: formatInt(data.exceptions.noted),
                })}
              </p>
              <Bars rows={bars(data.exceptions.by_reason)} label={reasonLabel} empty={t("dash.noExceptions")} testId="am-dash-reasons" />
              {data.exceptions.choices?.length ? (
                <>
                  <h3 className="mt-3 text-xs font-medium text-text-secondary">{t("dash.choices")}</h3>
                  <Bars rows={bars(data.exceptions.choices)} label={(k) => k || "—"} testId="am-dash-choices" />
                </>
              ) : null}
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.questions")}</h2>
              <Bars rows={bars(data.questions)} label={reasonLabel} empty={t("dash.noQuestions")} testId="am-dash-questions" />
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.failures")}</h2>
              <Bars rows={bars(data.failures)} label={(k) => k} empty={t("dash.noFailures")} testId="am-dash-failures" />
            </Card>

            {data.open?.length ? (
              <Card>
                <h2 className="text-sm font-semibold text-text-primary">{t("dash.open")}</h2>
                {/* the gate's code names the words; it is never shown itself */}
                <Bars rows={bars(data.open)} label={(k) => t(outcomeKey(COMPLETED_OPEN, k) ?? "outcome.open.gate")} testId="am-dash-open" />
              </Card>
            ) : null}

            {Object.keys(data.confidence ?? {}).length ? (
              <Card>
                <h2 className="text-sm font-semibold text-text-primary">{t("dash.confidence")}</h2>
                <Bars rows={recordBars(data.confidence, CONFIDENCE_ORDER)} label={(k) => labelOf(t, "dash.conf.", k)} testId="am-dash-confidence" />
              </Card>
            ) : null}

            {Object.keys(data.tiers ?? {}).length ? (
              <Card>
                <h2 className="text-sm font-semibold text-text-primary">{t("dash.tiers", { n: formatInt(data.documents) })}</h2>
                <Bars rows={recordBars(data.tiers, TIER_ORDER)} label={(k) => labelOf(t, "dash.tier.", k)} testId="am-dash-tiers" />
              </Card>
            ) : null}
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.models")}</h2>
              {data.models?.length ? (
                <table className="mt-2 w-full text-left text-xs" data-testid="am-dash-models">
                  <thead className="text-text-muted">
                    <tr>
                      <th scope="col" className="py-1 font-normal">{t("dash.model")}</th>
                      <th scope="col" className="py-1 font-normal">{t("dash.where")}</th>
                      <th scope="col" className="py-1 text-right font-normal">{t("dash.runs")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.models.map((m) => (
                      <tr key={`${m.model}-${m.self_hosted}`} className="border-t border-border-subtle">
                        <td className="break-all py-1.5 pr-3 text-text-primary">{m.model}</td>
                        <td className="py-1.5 pr-3">
                          <Pill tone={m.self_hosted ? "success" : "warning"}>{m.self_hosted ? t("dash.inOrg") : t("dash.external")}</Pill>
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-text-primary">{formatInt(m.runs)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="mt-1.5 text-xs text-text-muted">{t("dash.noModels")}</p>
              )}
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("dash.versionsTitle")}</h2>
              {data.versions?.length ? (
                <ul className="mt-2 flex flex-col gap-1 text-xs text-text-secondary" data-testid="am-dash-versions">
                  {data.versions.map((v) => (
                    <li key={v.version}>
                      {t("dash.versionLine", { v: v.version, n: formatInt(v.runs), when: formatDateTime(v.last_run_at, locale) })}
                    </li>
                  ))}
                </ul>
              ) : null}
              {data.artifacts?.length ? (
                <p className="mt-2 text-xs text-text-secondary">
                  {t("dash.artifacts", { list: data.artifacts.map((a) => `${a.key} × ${formatInt(a.count)}`).join(", ") })}
                </p>
              ) : null}
              <SelfTestLine d={data} />
            </Card>
          </div>
        </>
      )}

      <p className="mt-6 text-xs text-text-muted">
        <Link href={settingsHref(installId)} className="underline underline-offset-2 hover:text-text-primary">
          {t("dash.toSettings")}
        </Link>
      </p>
    </PageShell>
  );
}

function labelOf(t: (k: AmKey) => string, prefix: string, key: string): string {
  const k = `${prefix}${key}` as AmKey;
  const s = t(k);
  return s === k ? key : s;
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "warning" | null }) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-dash-tile">
      <div className="truncate text-xs text-text-secondary">{label}</div>
      <div className={cls("mt-1 truncate text-xl font-semibold tabular-nums", tone === "warning" ? "text-status-warning" : "text-text-primary")}>{value}</div>
      {sub ? <div className="mt-0.5 truncate text-xs text-text-muted">{sub}</div> : null}
    </div>
  );
}

function Bars({ rows, label, empty, testId }: { rows: Bar[]; label: (key: string) => string; empty?: string; testId: string }) {
  const t = useAmT();
  if (!rows.length) return empty ? <p className="mt-1.5 text-xs text-text-muted">{empty}</p> : null;
  return (
    <ul className="mt-2 flex flex-col gap-2" data-testid={testId}>
      {rows.map((r) => (
        <li key={r.key} data-testid="am-dash-bar" data-key={r.key}>
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="min-w-0 break-words text-text-primary">{label(r.key)}</span>
            <span className="shrink-0 tabular-nums text-text-secondary">
              {formatInt(r.count)}
              {r.open ? <span className="text-status-warning">{` · ${t("dash.openN", { n: formatInt(r.open) })}`}</span> : null}
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-elevated" aria-hidden>
            <div className="h-full rounded-full bg-accent" style={{ width: `${r.pct}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function StatusLine({ d }: { d: AgentDashboard }) {
  const t = useAmT();
  // AM6: by what the runs came to, when the server says it
  const entries = Object.entries(d.runs.by_outcome ?? d.runs.by_status ?? {}).filter(([, n]) => n > 0);
  if (!entries.length) return null;
  const name = (s: string) => (s === COMPLETED_OPEN ? t(openCountKey(d)) : STATUS_KEYS[s] ? t(STATUS_KEYS[s]!) : s);
  return (
    <p className="mt-2 text-xs text-text-muted" data-testid="am-dash-states">
      {entries.map(([s, n]) => `${name(s)} ${formatInt(n)}`).join(" · ")}
    </p>
  );
}

function SelfTestLine({ d }: { d: AgentDashboard }) {
  const t = useAmT();
  const locale = useAmLocale();
  const st = d.last_self_test;
  if (!st) return null;
  // AM6: a self-test that finished open is named so; its reason is a code, and the name has said it
  const open = st.outcome === COMPLETED_OPEN;
  const status = open ? t(outcomeKey(COMPLETED_OPEN, st.reason) ?? "outcome.open.gate") : STATUS_KEYS[st.status] ? t(STATUS_KEYS[st.status]!) : st.status;
  return (
    <p className="mt-2 text-xs text-text-muted" data-testid="am-dash-selftest">
      {t("dash.selfTest", { status, v: st.version ?? "", when: st.finished_at ? formatDateTime(st.finished_at, locale) : "—" })}
      {st.reason && !open ? ` · ${st.reason}` : null}
    </p>
  );
}
