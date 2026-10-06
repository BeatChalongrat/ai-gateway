"use client";

// /chat/agents/runs/[runId] — the evidence view (board 4): what the agent
// did, step by step; what it asked; every number with the file, sheet and
// cells it came from; what needs review; every file, every version.
// Refreshes itself while the run is still working.
//
// AM4: ?tab=numbers opens it on "ที่มาของตัวเลข" (the workspace header's one
// action). AM7: an admin opens someone else's run in the admin app, after
// stating a reason; the owner is told, and this page lists every opening.
//
// AM6 — the run's state goes by what it came to. A round that delivered its
// draft while the agent's gate did not pass is finished, with a note: its
// gate is a step that left a verdict, not one that failed. It is still not
// signed, and the page says why.

import { createContext, useContext, useEffect, useId, useMemo, useState } from "react";
import { ChevronDown, Download } from "lucide-react";

import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/fileUtil";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey, type AmT } from "../i18n";
import {
  DASH,
  elapsedBetween,
  formatCredits,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatPct,
  formatSignedNumber,
  formatSignedPct,
  signOf,
} from "../format";
import { COMPLETED_OPEN, outcomeKey, outcomeOf, outcomeTone } from "../outcome";
import type { AmFile, Artifact, Clarification, Computed, ComputedLine, Review, RunDetail, Step } from "../types";
import { BackLink, Card, CheckLine, cls, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill } from "../ui";
import { viaKey } from "../workspace/AgentQuestionCard";
import { workspaceHref } from "../workspace/context";
import { ArtifactRow, qualityOf, SignOffUnavailable } from "../workspace/OutputsCard";
import { ExceptionsPanel, useRunExceptions } from "../exceptions/ExceptionsPanel";
import { StepIcon } from "../workspace/ProgressCard";
import { ProblemButton } from "../problem/ProblemButton";
import { ReviewsList, SignOffButton } from "../review/SignOff";
import { ShareButton } from "../share/ShareButton";

const ACTIVE = new Set(["queued", "running", "awaiting_input"]);
const POLL_MS = 3000;

type Tab = "overview" | "steps" | "questions" | "numbers" | "exceptions" | "files";
const TABS: readonly Tab[] = ["overview", "steps", "questions", "numbers", "exceptions", "files"];
const isTab = (x: string | null | undefined): x is Tab => !!x && (TABS as readonly string[]).includes(x);

const KIND_KEY: Record<string, AmKey> = {
  ingest: "kind.ingest",
  check: "kind.check",
  clarification: "kind.clarification",
  compute: "kind.compute",
  ai: "kind.ai",
  deliverable: "kind.deliverable",
  publish: "kind.publish",
};

const STEP_STATUS: Record<string, string> = {
  succeeded: "done",
  running: "running",
  awaiting: "waiting",
  failed: "failed",
  skipped: "skipped",
};

/** AM6: the steps that hold the gate's verdict of a round that finished open. */
const VERDICT_STEPS = new Set(["gate", "stopped"]);
const OpenRound = createContext(false);

/** A step's state as its icon reads it; a verdict step of a round that
 *  finished open is "open" — finished, with a note. */
function stepState(s: Step, open: boolean): string {
  if (open && s.status === "failed" && VERDICT_STEPS.has(s.name)) return "open";
  return STEP_STATUS[s.status] ?? "pending";
}

/** A step row's name in words. */
export function stepLabel(name: string, t: AmT): string {
  if (name.startsWith("parse:")) return t("step.parse", { file: name.slice(6) });
  if (name.startsWith("preview:")) return t("step.preview", { file: name.slice(8) });
  if (name.startsWith("clarify:")) {
    if (name.includes("postcompute")) return t("step.clarify.postcompute");
    if (name.includes("preflight")) return t("step.clarify.preflight");
    return t("step.clarify.other");
  }
  switch (name) {
    case "preflight":
      return t("step.preflight");
    case "compute":
      return t("step.compute");
    case "postcompute":
      return t("step.postcompute");
    case "narrative":
      return t("step.narrative");
    case "render":
      return t("step.render");
    case "publish":
      return t("step.publish");
    case "gate":
      return t("step.gate");
    case "stopped":
      return t("step.stopped");
  }
  return name;
}

export function RunDetailPage({ runId, initialTab }: { runId: string; initialTab?: string | null }) {
  return (
    <FeatureGate>
      <RunDetailView runId={runId} initialTab={initialTab} />
    </FeatureGate>
  );
}

/** AM4: the file downloads of the page (the owner's). */
const RunDownloads = createContext<{ artifact?: (id: string, name: string) => Promise<void>; file: (id: string, name: string) => Promise<void> }>({
  file: (id, name) => amApi.downloadFile(id, name),
});

function RunDetailView({ runId, initialTab }: { runId: string; initialTab?: string | null }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [data, setData] = useState<RunDetail | null>(null);
  const [computed, setComputed] = useState<Computed | null>(null);
  const [computedMissing, setComputedMissing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [tab, setTab] = useState<Tab>(isTab(initialTab) ? initialTab : "overview");
  const tabsId = useId();
  const downloads = useMemo(() => ({ file: (id: string, name: string) => amApi.downloadFile(id, name) }), []);

  const status = data?.run.status ?? "";
  const outcome = data ? outcomeOf(data.run) : "";
  const finishedOpen = outcome === COMPLETED_OPEN && data?.run.kind === "run";
  const active = ACTIVE.has(status);
  // what the run left for a person to decide (the expense audit): read once it has finished
  const decisions = useRunExceptions(runId, !!data && !active);
  // AM3: who signed this run off (re-read after a sign-off, and while its stamp job works)
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [reviewsTick, setReviewsTick] = useState(0);
  const finishedRun = status === "succeeded" && data?.run.kind === "run";
  useEffect(() => {
    if (!finishedRun) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      amApi.reviews(runId).then(
        (r) => {
          if (!alive) return;
          const list = r?.reviews ?? [];
          setReviews(list);
          if (list.some((x) => x.stamp_status === "queued" || x.stamp_status === "running")) timer = setTimeout(() => void load(), POLL_MS);
        },
        () => {},
      );
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId, finishedRun, reviewsTick]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const d = await amApi.run(runId);
        if (!alive) return;
        setData(d);
        setError(null);
        try {
          const c = await amApi.computed(runId);
          if (alive) {
            setComputed(c);
            setComputedMissing(false);
          }
        } catch (err) {
          if (alive && err instanceof AmError && err.status === 404) setComputedMissing(true);
        }
        if (alive && ACTIVE.has(d.run.status)) timer = setTimeout(() => void load(), POLL_MS);
      } catch (err) {
        if (alive) setError(err);
      }
    };
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId, reload]);

  const steps = useMemo(() => [...(data?.steps ?? [])].sort((a, b) => Date.parse(a.started_at ?? "") - Date.parse(b.started_at ?? "") || a.seq - b.seq), [data]);
  const cards = data?.clarifications ?? [];
  const exceptions = computed?.exceptions ?? [];
  const artifacts = data?.artifacts ?? [];
  const files = data?.files ?? [];

  if (error && !data) {
    return (
      <PageShell>
        <BackLink href="/chat/agents">{t("ws.backToMarket")}</BackLink>
        <div className="mt-6">
          <ErrorPanel
            title={error instanceof AmError && error.status === 404 ? t("run.notFound") : t("run.loadFailed")}
            message={errorMessage(error)}
            onRetry={() => setReload((n) => n + 1)}
          />
        </div>
      </PageShell>
    );
  }
  if (!data) return <PageSkeleton />;

  const run = data.run;
  const period = computed?.period_label || run.period || "";
  const duration = elapsedBetween(run.started_at, run.finished_at);
  const tabs: { id: Tab; key: AmKey; count?: number }[] = [
    { id: "overview", key: "run.tab.overview" },
    { id: "steps", key: "run.tab.steps", count: steps.length },
    { id: "questions", key: "run.tab.questions", count: cards.length },
    { id: "numbers", key: "run.tab.numbers" },
    { id: "exceptions", key: "run.tab.exceptions", count: exceptions.length + (decisions.rows?.length ?? 0) },
    { id: "files", key: "run.tab.files", count: artifacts.length + files.length },
  ];

  return (
    <PageShell width="max-w-6xl">
      <BackLink href={workspaceHref(data.install.id)}>{t("run.back")}</BackLink>

      <div className="mt-4 flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold text-text-primary sm:text-xl">
            {data.install.title}
            {period ? <span className="font-normal text-text-muted">{` · ${t("run.period", { period })}`}</span> : null}
          </h1>
          <p className="mt-1 text-xs text-text-muted">
            {[
              t("run.startedAt", { when: formatDateTime(run.started_at ?? run.created_at, locale) }),
              run.kind === "self_test" ? t("run.selfTest") : t("run.manual"),
              run.definition_version ? t("run.agentVersion", { v: run.definition_version }) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <span data-testid="am-run-status" data-outcome={outcome}>
          <Pill tone={outcomeTone(outcome)} size="md">
            {active ? <Spinner size={11} /> : null}
            <span>
              {t(outcomeKey(outcome, run.status_reason) ?? "progress.title.running")}
              {duration > 0 ? ` · ${formatDuration(duration, t)}` : null}
            </span>
          </Pill>
        </span>
      </div>

      {run.evidence_removed_at ? (
        <p role="note" className="mt-3 rounded-lg border border-border bg-bg-surface px-3 py-2 text-xs text-text-secondary">
          {t("run.evidenceRemoved")}
        </p>
      ) : null}
      {data.access_log?.length ? (
        <section className="mt-3 rounded-lg border border-border-subtle bg-bg-surface px-3 py-2" aria-label={t("run.accessLog")} data-testid="am-run-access-log">
          <h2 className="text-xs font-semibold text-text-primary">{t("run.accessLog")}</h2>
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-text-secondary">
            {data.access_log.map((a) => (
              <li key={a.opened_at}>{t("run.accessLine", { who: a.admin_name || a.admin_email, when: a.at_label, reason: a.reason })}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="am-run-actions">
        {finishedRun ? (
          <SignOffButton
            runId={runId}
            onDone={() => {
              setReviewsTick((n) => n + 1);
            }}
          />
        ) : null}
        {run.kind === "run" ? <ShareButton runId={runId} /> : null}
        <ProblemButton target={{ kind: "run", runId }} />
        {finishedOpen ? <SignOffUnavailable why={computed?.gate?.what} /> : null}
      </div>
      {reviews && reviews.length ? (
        <section className="mt-3" aria-label={t("review.listTitle")}>
          <ReviewsList reviews={reviews} />
        </section>
      ) : null}

      <div role="tablist" aria-label={t("run.tabs")} className="scrollbar-thin mt-5 flex gap-1 overflow-x-auto border-b border-border-subtle">
        {tabs.map((tb) => (
          <button
            key={tb.id}
            type="button"
            role="tab"
            id={`${tabsId}-tab-${tb.id}`}
            aria-selected={tab === tb.id}
            aria-controls={`${tabsId}-panel`}
            onClick={() => setTab(tb.id)}
            className={cls(
              "focus-ring -mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors",
              tab === tb.id ? "border-text-primary font-medium text-text-primary" : "border-transparent text-text-secondary hover:text-text-primary",
            )}
            data-testid={`am-run-tab-${tb.id}`}
          >
            {t(tb.key)}
            {tb.count ? <span className="ml-1.5 text-xs tabular-nums text-text-muted">{tb.count}</span> : null}
          </button>
        ))}
      </div>

      <div id={`${tabsId}-panel`} role="tabpanel" aria-labelledby={`${tabsId}-tab-${tab}`} className="mt-5">
        {tab === "overview" ? (
          <OpenRound.Provider value={outcome === COMPLETED_OPEN}>
            <Overview data={data} computed={computed} steps={steps} />
          </OpenRound.Provider>
        ) : tab === "steps" ? (
          <OpenRound.Provider value={outcome === COMPLETED_OPEN}>
            <StepsTab steps={steps} />
          </OpenRound.Provider>
        ) : tab === "questions" ? (
          <QuestionsTab cards={cards} />
        ) : tab === "numbers" ? (
          computed ? <NumbersTab c={computed} /> : <Empty text={computedMissing || !active ? t("run.noComputed") : t("common.loading")} />
        ) : tab === "exceptions" ? (
          <ExceptionsTab
            c={computed}
            decisions={
              decisions.rows?.length ? <ExceptionsPanel rows={decisions.rows} onDecided={decisions.replace} onStale={decisions.reload} /> : null
            }
          />
        ) : (
          <RunDownloads.Provider value={downloads}>
            <FilesTab artifacts={artifacts} files={files} />
          </RunDownloads.Provider>
        )}
      </div>
    </PageShell>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="rounded-xl border border-border-subtle px-4 py-6 text-center text-sm text-text-muted">{text}</p>;
}

// ── overview ───────────────────────────────────────────────────────────

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string | null; tone?: "success" | "warning" | "danger" | null }) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-bg-surface px-4 py-3">
      <div className="truncate text-xs text-text-secondary">{label}</div>
      <div className="mt-1 truncate text-xl font-semibold tabular-nums text-text-primary">{value}</div>
      {sub ? (
        <div
          className={cls(
            "mt-0.5 truncate text-xs tabular-nums",
            tone === "success" ? "text-status-success" : tone === "warning" ? "text-status-warning" : tone === "danger" ? "text-status-danger" : "text-text-muted",
          )}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
}

const KPI_LINES: { key: string; label: AmKey; upIsGood: boolean }[] = [
  { key: "revenue", label: "run.kpi.revenue", upIsGood: true },
  { key: "total_expenses", label: "run.kpi.expenses", upIsGood: false },
  { key: "net_profit", label: "run.kpi.netProfit", upIsGood: true },
];

function lineOf(c: Computed | null, key: string): ComputedLine | undefined {
  return (c?.lines ?? []).find((l) => l.key === key);
}

function Overview({ data, computed, steps }: { data: RunDetail; computed: Computed | null; steps: Step[] }) {
  const t = useAmT();
  const open = useContext(OpenRound);
  const run = data.run;
  const hasPrior = !!computed?.has_prior;
  const kpi = (key: string, label: AmKey, upIsGood: boolean) => {
    const l = lineOf(computed, key);
    if (!l) return null;
    const s = signOf(l.var_prior_pct);
    const tone = !hasPrior || l.var_prior_pct == null || s === 0 ? null : (s > 0) === upIsGood ? "success" : upIsGood ? "danger" : "warning";
    return (
      <Kpi
        key={key}
        label={t(label)}
        value={formatMoney(l.current)}
        sub={hasPrior && l.var_prior_pct != null ? t("run.kpi.vsPrior", { pct: formatSignedPct(l.var_prior_pct) }) : null}
        tone={tone}
      />
    );
  };
  const margin = (computed?.ratios ?? []).find((r) => r.key === "net_margin");
  const marginSign = signOf(margin?.change_points);
  const assumptions = run.assumptions ?? [];
  const latest = latestArtifacts(data.artifacts ?? []);

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <Card className="order-2 lg:order-1">
        <h2 className="text-sm font-semibold text-text-primary">{t("run.whatHappened")}</h2>
        <ol className="mt-3 flex flex-col gap-2.5">
          {steps.map((s) => (
            <li key={s.id} className="flex gap-2.5">
              <span className="mt-0.5">
                <StepIcon status={stepState(s, open)} />
              </span>
              <div className="min-w-0">
                <div className="truncate text-sm text-text-primary">{stepLabel(s.name, t)}</div>
                <div className="truncate text-[11px] text-text-muted">
                  {[s.script_id || s.model_id, s.duration_ms != null ? formatDuration(s.duration_ms, t) : null].filter(Boolean).join(" · ")}
                </div>
              </div>
            </li>
          ))}
        </ol>
        <p className="mt-4 rounded-lg bg-bg-base px-3 py-2.5 text-xs leading-relaxed text-text-secondary">{t("run.scriptNote")}</p>
        <p className="mt-3 text-xs text-text-muted">
          {t("run.credits", { n: formatCredits(run.credits_spent ?? 0) })}
          {data.budget ? ` · ${t("run.budget", { credits: formatCredits(data.budget.credits), spent: formatCredits(data.budget.spent_this_month) })}` : null}
        </p>
      </Card>

      <div className="order-1 flex min-w-0 flex-col gap-4 lg:order-2">
        {computed ? (
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            {KPI_LINES.map((k) => kpi(k.key, k.label, k.upIsGood))}
            {margin ? (
              <Kpi
                label={t("run.kpi.netMargin")}
                value={formatPct(margin.current)}
                sub={hasPrior && margin.change_points != null ? t("run.kpi.points", { n: formatSignedNumber(margin.change_points) }) : null}
                tone={marginSign === 0 ? null : marginSign > 0 ? "success" : "danger"}
              />
            ) : null}
          </div>
        ) : null}

        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("run.model")}</h2>
          <p className="mt-1 text-sm text-text-secondary">
            {run.model_used ? `${run.model_used} · ${run.model_self_hosted ? t("run.inOrg") : t("run.external")}` : t("run.modelNone")}
          </p>
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-text-primary">{t("run.filesProduced")}</h2>
          {latest.length ? (
            <>
              <ul className="mt-2.5 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
                {latest.map((a) => (
                  <ArtifactRow key={a.id} artifact={a} />
                ))}
              </ul>
              <QualityList artifacts={latest} />
            </>
          ) : (
            <p className="mt-1.5 text-sm text-text-muted">{t("files.none")}</p>
          )}
        </Card>

        {assumptions.length ? (
          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("run.assumptions")}</h2>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-sm text-text-secondary">
              {assumptions.map((a, i) => (
                <li key={`${a.code}-${i}`}>{a.text}</li>
              ))}
            </ul>
          </Card>
        ) : null}
      </div>
    </div>
  );
}

/** The newest version of each format. */
function latestArtifacts(all: Artifact[]): Artifact[] {
  const by = new Map<string, Artifact>();
  for (const a of all) {
    const cur = by.get(a.format);
    if (!cur || a.version > cur.version) by.set(a.format, a);
  }
  const order = ["docx", "pdf", "pptx", "xlsx", "template"];
  return [...by.values()].sort((a, b) => order.indexOf(a.format) - order.indexOf(b.format));
}

function QualityList({ artifacts }: { artifacts: Artifact[] }) {
  const t = useAmT();
  const q = qualityOf(artifacts.map((a) => ({ quality: a.validation })));
  if (!q.total) return null;
  return (
    <div className="mt-3">
      {/* the same three sentences as the files card (the sentence takes {failed}; this passed {pass}/{total}, so "{failed}" was printed as written) */}
      <div className="text-xs font-medium text-text-secondary">
        {q.pass !== q.total
          ? t("outputs.quality", { failed: String(q.total - q.pass) })
          : q.values > 0
            ? t("outputs.qualityValues", { values: String(q.values) })
            : t("outputs.qualityAll")}
      </div>
      <ul className="mt-1.5 flex flex-col gap-1">
        {q.checks.map((c, i) => (
          <CheckLine key={`${c.name ?? c.label}-${i}`} ok={c.ok}>
            {c.label || c.name}
            {c.detail ? ` · ${c.detail}` : null}
          </CheckLine>
        ))}
      </ul>
    </div>
  );
}

// ── steps ──────────────────────────────────────────────────────────────

const MAX_JSON = 4000;
const IO_KEYS: AmKey[] = ["step.input", "step.output"];

function pretty(v: unknown, t: AmT): string {
  if (v === null || v === undefined) return "";
  let s: string;
  try {
    s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    s = String(v);
  }
  return s.length > MAX_JSON ? `${s.slice(0, MAX_JSON)}\n${t("step.truncated")}` : s;
}

function StepsTab({ steps }: { steps: Step[] }) {
  const t = useAmT();
  if (!steps.length) return <Empty text={t("steps.empty")} />;
  return (
    <ol className="flex flex-col gap-2" data-testid="am-run-steps">
      {steps.map((s) => (
        <StepRow key={s.id} s={s} />
      ))}
    </ol>
  );
}

/** A step's script: a file name reads as a name; the runner's own rule-written narrative is said in words (AM4), in the reader's language. */
const SCRIPT_KEY: Record<string, AmKey> = { fallback_narrative: "step.script.rules" };
export function scriptLabel(script: string, t: AmT): string {
  return SCRIPT_KEY[script] ? t(SCRIPT_KEY[script]!) : script;
}

function StepRow({ s }: { s: Step }) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const panel = useId();
  const status = stepState(s, useContext(OpenRound));
  const input = pretty(s.input, t);
  const output = pretty(s.output, t);
  const meta = [
    KIND_KEY[s.kind] ? t(KIND_KEY[s.kind]!) : s.kind,
    s.kind === "clarification" && s.duration_ms != null
      ? t("step.waited", { d: formatDuration(s.duration_ms, t) })
      : s.duration_ms != null
        ? formatDuration(s.duration_ms, t)
        : null,
    s.attempt && s.attempt > 1 ? t("step.attempt", { n: String(s.attempt) }) : null,
    s.script_id ? scriptLabel(s.script_id, t) : null,
    s.model_id || null,
    s.credits ? t("step.credits", { n: formatCredits(s.credits) }) : null,
  ].filter(Boolean) as string[];
  return (
    <li className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-run-step" data-step-status={status}>
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5">
          <StepIcon status={status} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-text-primary">{stepLabel(s.name, t)}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[11px] text-text-muted">
            {meta.map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </div>
          {s.error ? (
            <p className={cls("mt-1.5 whitespace-pre-wrap break-words text-xs", status === "open" ? "text-text-secondary" : "text-status-danger")}>{s.error}</p>
          ) : null}
        </div>
        {input || output ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panel}
            aria-label={t("step.details")}
            onClick={() => setOpen((v) => !v)}
            className="focus-ring tap-44 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          >
            <ChevronDown width={15} height={15} aria-hidden className={cls("transition-transform", open ? "rotate-180" : "")} />
          </button>
        ) : null}
      </div>
      {open ? (
        <div id={panel} className="mt-3 grid gap-3 md:grid-cols-2">
          {IO_KEYS.map((k, i) => ({ k, v: i === 0 ? input : output })).map(({ k, v }) =>
            v ? (
              <div key={k} className="min-w-0">
                <div className="text-[11px] font-medium text-text-secondary">{t(k)}</div>
                <pre className="scrollbar-thin mt-1 max-h-80 overflow-auto rounded-lg bg-bg-base px-3 py-2 font-mono text-[11px] leading-relaxed text-text-primary">{v}</pre>
              </div>
            ) : null,
          )}
        </div>
      ) : null}
    </li>
  );
}

// ── questions ──────────────────────────────────────────────────────────

const MOMENT_KEY: Record<string, AmKey> = {
  preflight: "q.moment.preflight",
  postcompute: "q.moment.postcompute",
  run: "q.moment.run",
};
const CARD_STATUS_KEY: Record<string, AmKey> = {
  open: "cardStatus.open",
  interpreting: "cardStatus.interpreting",
  answered: "cardStatus.answered",
  defaulted: "cardStatus.defaulted",
  superseded: "cardStatus.superseded",
  cancelled: "cardStatus.cancelled",
};

function QuestionsTab({ cards }: { cards: Clarification[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  if (!cards.length) return <Empty text={t("q.empty")} />;
  return (
    <div className="flex flex-col gap-3" data-testid="am-run-questions">
      {cards.map((c) => {
        const answers = c.answer ?? {};
        return (
          <Card key={c.id}>
            <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
              {MOMENT_KEY[c.moment] ? <span>{t(MOMENT_KEY[c.moment]!)}</span> : null}
              <Pill tone={c.status === "open" ? "warning" : c.status === "answered" ? "success" : "neutral"}>
                {CARD_STATUS_KEY[c.status] ? t(CARD_STATUS_KEY[c.status]!) : c.status}
              </Pill>
              {c.answered_at ? <span>{t("q.answeredAt", { when: formatDateTime(c.answered_at, locale) })}</span> : null}
            </div>
            <ul className="mt-2 flex flex-col gap-3">
              {(c.questions ?? []).map((q) => {
                const a = answers[q.key];
                const via = viaKey(a?.via ?? c.answered_via);
                return (
                  <li key={q.key}>
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="rounded bg-bg-elevated px-1.5 py-0.5 text-[11px] font-medium text-text-secondary">{q.header}</span>
                      <span className="text-sm text-text-primary">{q.question}</span>
                    </div>
                    {a?.label ? (
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-medium text-text-primary">{a.label}</span>
                        {via ? <Pill>{t(via)}</Pill> : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {c.answer_text ? <p className="mt-2 text-xs text-text-secondary">{t("q.typed", { text: c.answer_text })}</p> : null}
          </Card>
        );
      })}
    </div>
  );
}

// ── numbers ────────────────────────────────────────────────────────────

const TH = "whitespace-nowrap px-3 py-2 text-left text-[11px] font-medium text-text-secondary";
const TD = "whitespace-nowrap px-3 py-2 text-text-primary";
const TDN = "whitespace-nowrap px-3 py-2 text-right tabular-nums text-text-primary";

function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="scrollbar-thin mt-2.5 overflow-x-auto rounded-lg border border-border-subtle">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function NumbersTab({ c }: { c: Computed }) {
  const t = useAmT();
  const prior = !!c.has_prior;
  const budget = !!c.has_budget;
  const lines = c.lines ?? [];
  const ratios = c.ratios ?? [];
  const accounts = c.accounts ?? [];
  const cites = c.citations ?? [];
  // an account's group is the key of the line it adds up to: name it as that line does
  const groupName = new Map(lines.map((l) => [l.key, l.label] as const));
  return (
    <div className="flex flex-col gap-6" data-testid="am-run-numbers">
      <section>
        <h2 className="text-sm font-semibold text-text-primary">{t("num.lines")}</h2>
        <Table>
          <thead className="bg-bg-elevated">
            <tr>
              <th scope="col" className={TH}>
                {t("num.col.item")}
              </th>
              <th scope="col" className={cls(TH, "text-right")}>
                {t("num.col.current")}
              </th>
              {prior ? (
                <>
                  <th scope="col" className={cls(TH, "text-right")}>
                    {c.prior_label || t("num.col.prior")}
                  </th>
                  <th scope="col" className={cls(TH, "text-right")}>
                    {t("num.col.varPrior")}
                  </th>
                </>
              ) : null}
              {budget ? (
                <>
                  <th scope="col" className={cls(TH, "text-right")}>
                    {t("num.col.budget")}
                  </th>
                  <th scope="col" className={cls(TH, "text-right")}>
                    {t("num.col.varBudget")}
                  </th>
                </>
              ) : null}
              <th scope="col" className={TH}>
                {t("num.col.id")}
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l) => (
              <tr key={l.key} className="border-t border-border-subtle">
                <td className={TD}>{l.label}</td>
                <td className={TDN}>{formatMoney(l.current)}</td>
                {prior ? (
                  <>
                    <td className={TDN}>{formatMoney(l.prior)}</td>
                    <td className={TDN}>{variance(l.var_prior, l.var_prior_pct)}</td>
                  </>
                ) : null}
                {budget ? (
                  <>
                    <td className={TDN}>{formatMoney(l.budget)}</td>
                    <td className={TDN}>{variance(l.var_budget, l.var_budget_pct)}</td>
                  </>
                ) : null}
                <td className={cls(TD, "font-mono text-[11px] text-text-muted")}>{l.cite ?? DASH}</td>
              </tr>
            ))}
          </tbody>
        </Table>
        {c.balance ? (
          <p className="mt-2 text-xs text-text-muted">
            {t("num.balance", { debit: formatMoney(c.balance.debit), credit: formatMoney(c.balance.credit), diff: formatMoney(c.balance.diff) })}
          </p>
        ) : null}
      </section>

      {ratios.length ? (
        <section>
          <h2 className="text-sm font-semibold text-text-primary">{t("num.ratios")}</h2>
          <Table>
            <thead className="bg-bg-elevated">
              <tr>
                <th scope="col" className={TH}>
                  {t("num.col.item")}
                </th>
                <th scope="col" className={cls(TH, "text-right")}>
                  {t("num.col.current")}
                </th>
                {prior ? (
                  <>
                    <th scope="col" className={cls(TH, "text-right")}>
                      {t("num.col.prior")}
                    </th>
                    <th scope="col" className={cls(TH, "text-right")}>
                      {t("num.col.change")}
                    </th>
                  </>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {ratios.map((r) => (
                <tr key={r.key} className="border-t border-border-subtle">
                  <td className={TD}>{r.label}</td>
                  <td className={TDN}>{formatPct(r.current)}</td>
                  {prior ? (
                    <>
                      <td className={TDN}>{formatPct(r.prior)}</td>
                      <td className={TDN}>{formatSignedNumber(r.change_points)}</td>
                    </>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </Table>
        </section>
      ) : null}

      {accounts.length ? (
        <section>
          <h2 className="text-sm font-semibold text-text-primary">{t("num.accounts")}</h2>
          <Table>
            <thead className="bg-bg-elevated">
              <tr>
                <th scope="col" className={TH}>
                  {t("num.col.code")}
                </th>
                <th scope="col" className={TH}>
                  {t("num.col.name")}
                </th>
                <th scope="col" className={TH}>
                  {t("num.col.group")}
                </th>
                <th scope="col" className={cls(TH, "text-right")}>
                  {t("num.col.current")}
                </th>
                {prior ? (
                  <>
                    <th scope="col" className={cls(TH, "text-right")}>
                      {t("num.col.prior")}
                    </th>
                    <th scope="col" className={cls(TH, "text-right")}>
                      {t("num.col.varPrior")}
                    </th>
                  </>
                ) : null}
                {budget ? (
                  <th scope="col" className={cls(TH, "text-right")}>
                    {t("num.col.budget")}
                  </th>
                ) : null}
                <th scope="col" className={cls(TH, "text-right")}>
                  {t("num.col.rows")}
                </th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.code} className="border-t border-border-subtle">
                  <td className={cls(TD, "font-mono text-xs")}>{a.code}</td>
                  <td className={cls(TD, "max-w-[16rem] truncate")} title={a.name}>
                    {a.name}
                  </td>
                  <td className={cls(TD, "text-text-secondary")}>{(a.group && groupName.get(a.group)) || a.group}</td>
                  <td className={TDN}>{formatMoney(a.current)}</td>
                  {prior ? (
                    <>
                      <td className={TDN}>{formatMoney(a.prior)}</td>
                      <td className={TDN}>{variance(a.var_prior, a.var_prior_pct)}</td>
                    </>
                  ) : null}
                  {budget ? <td className={TDN}>{formatMoney(a.budget)}</td> : null}
                  <td className={TDN}>{a.rows ?? DASH}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </section>
      ) : null}

      {cites.length ? (
        <section>
          <h2 className="text-sm font-semibold text-text-primary">{t("num.citations")}</h2>
          <Table>
            <thead className="bg-bg-elevated">
              <tr>
                <th scope="col" className={TH}>
                  {t("num.col.id")}
                </th>
                <th scope="col" className={TH}>
                  {t("num.col.item")}
                </th>
                <th scope="col" className={cls(TH, "text-right")}>
                  {t("num.col.value")}
                </th>
                <th scope="col" className={TH}>
                  {t("num.col.source")}
                </th>
                <th scope="col" className={TH}>
                  {t("num.col.method")}
                </th>
              </tr>
            </thead>
            <tbody>
              {cites.map((ci) => (
                <tr key={ci.id} className="border-t border-border-subtle align-top">
                  <td className={cls(TD, "font-mono text-[11px] text-text-muted")}>{ci.id}</td>
                  <td className={cls(TD, "max-w-[14rem] whitespace-normal")}>{ci.label}</td>
                  <td className={TDN}>{formatMoney(ci.value)}</td>
                  <td className={cls(TD, "whitespace-normal text-xs text-text-secondary")}>
                    {(ci.sources ?? []).map((s, i) => (
                      <div key={i}>{[s.file, s.sheet, s.cells].filter(Boolean).join(" · ")}</div>
                    ))}
                    {ci.derived_from?.length ? <div className="text-text-muted">{t("num.derived", { ids: ci.derived_from.join(", ") })}</div> : null}
                  </td>
                  <td className={cls(TD, "whitespace-normal text-xs text-text-secondary")}>
                    {[ci.script, ci.formula].filter(Boolean).join(" · ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </section>
      ) : null}
    </div>
  );
}

function variance(amount: string | null | undefined, pct: string | null | undefined): string {
  if (amount == null) return DASH;
  const a = formatMoney(amount);
  return pct == null ? a : `${a} (${formatSignedPct(pct)})`;
}

// ── exceptions ─────────────────────────────────────────────────────────

function ExceptionsTab({ c, decisions }: { c: Computed | null; decisions: React.ReactNode }) {
  const t = useAmT();
  const list = c?.exceptions ?? [];
  const excluded = c?.excluded ?? [];
  // an agent that leaves its exceptions for a person (the expense audit) shows them here, decisions and all
  if (decisions && !list.length && !excluded.length) {
    return (
      <div className="flex flex-col gap-4" data-testid="am-run-exceptions">
        {decisions}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4" data-testid="am-run-exceptions">
      {decisions}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-text-primary">{t("run.tab.exceptions")}</h2>
        {list.length ? <Pill tone="warning">{String(list.length)}</Pill> : null}
        <span className="flex-1" />
        <span className="text-xs text-text-muted">{t("exc.note")}</span>
      </div>
      {list.length ? (
        <ul className="flex flex-col gap-2">
          {list.map((e, i) => (
            <li key={`${e.code}-${i}`} className="rounded-xl border border-border border-l-status-warning bg-bg-surface px-4 py-3" style={{ borderLeftWidth: 3 }}>
              <div className="text-sm text-text-primary">{e.title}</div>
              {e.detail ? <div className="mt-0.5 text-xs text-text-secondary">{e.detail}</div> : null}
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-text-muted">
                {e.account ? <span>{t("exc.account", { code: e.account })}</span> : null}
                {e.amount ? <span>{t("exc.amount", { amount: formatMoney(e.amount) })}</span> : null}
                {e.rows?.length ? <span>{t("exc.rows", { rows: e.rows.slice(0, 12).join(", ") })}</span> : null}
                <span className="font-mono">{t("exc.code", { code: e.code })}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <Empty text={c ? t("exc.empty") : t("run.noComputed")} />
      )}
      {excluded.length ? (
        <section>
          <h3 className="text-xs font-medium text-text-secondary">{t("exc.excluded")}</h3>
          <ul className="mt-1.5 flex flex-col gap-1 text-sm text-text-secondary">
            {excluded.map((x, i) => (
              <li key={`${x.code}-${i}`}>
                {x.reason}
                {typeof x.rows === "number" ? <span className="text-text-muted">{` · ${t("exc.excludedRows", { n: String(x.rows) })}`}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

// ── files ──────────────────────────────────────────────────────────────

function FilesTab({ artifacts, files }: { artifacts: Artifact[]; files: AmFile[] }) {
  const t = useAmT();
  const sorted = [...artifacts].sort((a, b) => a.format.localeCompare(b.format) || b.version - a.version);
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="am-run-files">
      <Card>
        <h2 className="text-sm font-semibold text-text-primary">{t("files.outputs")}</h2>
        {sorted.length ? (
          <ul className="mt-2.5 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
            {sorted.map((a) => (
              <ArtifactRowIn key={a.id} a={a} />
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-sm text-text-muted">{t("files.none")}</p>
        )}
      </Card>
      <Card>
        <h2 className="text-sm font-semibold text-text-primary">{t("files.inputs")}</h2>
        {files.length ? (
          <ul className="mt-2.5 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
            {files.map((f) => (
              <InputFileRow key={f.id} f={f} />
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-sm text-text-muted">{t("files.none")}</p>
        )}
      </Card>
    </div>
  );
}

function ArtifactRowIn({ a }: { a: Artifact }) {
  const dl = useContext(RunDownloads);
  return <ArtifactRow artifact={a} download={dl.artifact ? () => dl.artifact!(a.id, a.name) : undefined} />;
}

const FILE_STATUS: Record<string, AmKey> = {
  active: "file.status.active",
  removed: "file.status.removed",
  pending_decision: "file.status.pending",
  next_round: "file.status.nextRound",
  staged: "file.status.staged",
};

function InputFileRow({ f }: { f: AmFile }) {
  const t = useAmT();
  const dl = useContext(RunDownloads);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const download = () => {
    setBusy(true);
    setError(null);
    dl.file(f.id, f.name).then(
      () => setBusy(false),
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };
  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-text-primary" title={f.name}>
          {f.name}
        </div>
        <div className="truncate font-mono text-[11px] text-text-muted" title={f.sha256}>
          {[formatBytes(f.size), f.sha256 ? `sha256 ${f.sha256.slice(0, 12)}…` : null, f.status ? t(FILE_STATUS[f.status] ?? "file.status.active") : null]
            .filter(Boolean)
            .join(" · ")}
        </div>
        {error ? (
          <div role="alert" className="text-[11px] text-status-danger">
            {error}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        onClick={download}
        disabled={busy}
        aria-label={t("common.downloadNamed", { name: f.name })}
        className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-text-secondary hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
      >
        {busy ? <Spinner size={15} /> : <Download width={16} height={16} aria-hidden />}
      </button>
    </li>
  );
}
