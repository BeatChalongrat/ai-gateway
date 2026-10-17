"use client";

import { useCallback, useEffect, useState } from "react";
import { Bar, Doughnut } from "react-chartjs-2";
import {
  Chart as ChartJS,
  ArcElement,
  BarElement,
  CategoryScale,
  LinearScale,
  Tooltip,
} from "chart.js";
import PageHeader from "../../components/ui/PageHeader";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { useApi } from "../../lib/useApi";
import { api, ApiError } from "../../lib/api";
import { useDateLocale, useT, type DictKey } from "../../lib/i18n";

ChartJS.register(ArcElement, BarElement, CategoryScale, LinearScale, Tooltip);

interface ProjectItem {
  project_id: string;
  name: string;
}

interface UsageByProject {
  projectId: string;
  projectName: string;
  requests: number;
  tokens: number;
  cost: number;
}

interface UsageByModel {
  provider: string;
  model: string;
  requests: number;
  tokens: number;
  cost: number;
}

interface UsageByUser {
  userId: string;
  username: string;
  email: string;
  projectName: string;
  requests: number;
  tokens: number;
  cost: number;
}

interface UsageSummary {
  project_id: string | null;
  total_requests: number;
  total_tokens: number;
  total_estimated_cost_usd: number;
}

interface ScanRunsSummary {
  total_runs: number;
  total_cost_usd: number;
  total_tokens: number;
  manual_runs: number;
  realtime_runs: number;
  avg_cost_per_run: number;
  last_run_at: string | null;
}

interface ScanRun {
  scan_run_id: string;
  trigger: string;
  provider: string;
  model: string;
  requests_scanned: number;
  findings_count: number;
  total_tokens: number;
  estimated_cost_usd: number;
  status: string;
  created_at: string | null;
}

interface ScanRunsResponse {
  items: ScanRun[];
  total: number;
}

interface AiReviewFindingsSummary {
  total_findings: number;
  new_findings: number;
  new_patterns: number;
  already_covered: number;
}

interface AiReviewFindingItem {
  scan_run_id: string | null;
  is_in_coverage: boolean;
}

type RunFilter = "all" | "findings" | "realtime" | "manual";

const ALL_PROJECTS = "";

type Tab = "user" | "system";

function formatCost(n: number | null | undefined): string {
  if (n == null || n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(6)}`;
  return `$${n.toFixed(4)}`;
}

// Abbreviate large token counts ("8.55M", "12.3K"); small counts keep commas.
function formatTokensShort(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

// "claude-haiku-4-5-20251001" → "claude-haiku-4.5": strip a trailing
// date stamp, then join a trailing major-minor pair with a dot.
function shortModel(m: string): string {
  return m.replace(/-\d{8}$/, "").replace(/-(\d+)-(\d+)$/, "-$1.$2");
}

// "Jun 22, 3:49 PM"
function formatRunTime(iso: string, dateLocale: string): string {
  const d = new Date(iso);
  return (
    d.toLocaleDateString(dateLocale, { month: "short", day: "numeric" }) +
    ", " +
    d.toLocaleTimeString(dateLocale, { hour: "numeric", minute: "2-digit" })
  );
}

function userInitials(name: string): string {
  const parts = name.split(/[^a-zA-Z0-9]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

const CHART_PALETTE = ["#3B82F6", "#8B5CF6", "#F59E0B", "#10B981"];
const DONUT_PALETTE = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B"];

const panelCard: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border-color)",
  borderRadius: 10,
  padding: "14px 16px",
};

const panelTitle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "var(--text-primary)",
};

// Chart.js can't read CSS variables, so axis/grid colors are resolved
// from the current data-theme and the charts re-render on toggle (the
// MutationObserver flips this state).
function useThemeIsDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const el = document.documentElement;
    const update = () => setDark(el.getAttribute("data-theme") === "dark");
    update();
    const obs = new MutationObserver(update);
    obs.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

// Stat card with a colored top accent line (usage-stat-card in globals.css).
function UsageStat({
  accent,
  label,
  value,
  sub,
  compact,
}: {
  accent: string;
  label: string;
  value: string;
  sub: string;
  compact?: boolean;
}) {
  return (
    <div
      className="usage-stat-card"
      style={{ ["--stat-accent" as string]: accent, ...(compact ? { padding: "12px 14px" } : {}) }}
    >
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 5 }}>{sub}</div>
    </div>
  );
}

export default function UsagePage() {
  const t = useT();
  const dateLocale = useDateLocale();
  const isDark = useThemeIsDark();
  const tickColor = isDark ? "#8B96BA" : "#6B6D78";
  const gridColor = isDark ? "#1F2A55" : "#ECEDF2";
  const projects = useApi<{ items: ProjectItem[] }>("/admin/projects");
  const byProject = useApi<UsageByProject[]>("/admin/usage/by-project");
  const byModel = useApi<UsageByModel[]>("/admin/usage/by-model");
  const [tab, setTab] = useState<Tab>("user");

  const [selectedProject, setSelectedProject] = useState<string>(ALL_PROJECTS);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [byUser, setByUser] = useState<UsageByUser[] | null>(null);

  const [systemSummary, setSystemSummary] = useState<ScanRunsSummary | null>(null);
  const [systemRuns, setSystemRuns] = useState<ScanRun[]>([]);
  const [findingsSummary, setFindingsSummary] = useState<AiReviewFindingsSummary | null>(null);
  const [findingsByRun, setFindingsByRun] = useState<Record<string, { newCount: number; covered: number }>>({});
  const [runFilter, setRunFilter] = useState<RunFilter>("all");
  const [loadingSystem, setLoadingSystem] = useState(false);
  const [systemError, setSystemError] = useState<string | null>(null);

  const loadUsage = useCallback(async (projectId: string) => {
    setUsageError(null);
    setLoadingUsage(true);
    try {
      const path = projectId
        ? `/admin/usage/summary?project_id=${encodeURIComponent(projectId)}`
        : "/admin/usage/summary";
      const data = await api.get(path);
      setUsage(data as UsageSummary);
    } catch (err) {
      const msg =
        typeof err === "object" && err && "body" in err
          ? ((err as { body?: { error?: { message?: string } } }).body?.error?.message ?? null)
          : null;
      // AM24: a role that may not see usage is told so in the page's language — the gateway's own sentence for
      // it ("Insufficient permissions for admin operations.") is English whatever the screen
      setUsageError(err instanceof ApiError && err.status === 403 ? t("obs.usage.noPermission") : msg || t("obs.usage.errorUser"));
      setUsage(null);
    } finally {
      setLoadingUsage(false);
    }
  }, [t]);

  const loadSystem = useCallback(async () => {
    setSystemError(null);
    setLoadingSystem(true);
    try {
      // 200 runs feed the 14-day activity chart; the table shows the
      // first 20. Findings are joined client-side (scan run rows only
      // carry a findings_count, not the new/covered split).
      const [summary, runs, fSummary, findings] = await Promise.all([
        api.get("/admin/ai-review/scan-runs/summary"),
        api.get("/admin/ai-review/scan-runs?limit=200"),
        api.get("/admin/ai-review/summary"),
        api.get("/admin/ai-review/findings?limit=500"),
      ]);
      setSystemSummary(summary as ScanRunsSummary);
      setSystemRuns(((runs as ScanRunsResponse).items) ?? []);
      setFindingsSummary(fSummary as AiReviewFindingsSummary);
      const byRun: Record<string, { newCount: number; covered: number }> = {};
      for (const f of ((findings as { items: AiReviewFindingItem[] }).items) ?? []) {
        if (!f.scan_run_id) continue;
        const rec = (byRun[f.scan_run_id] ??= { newCount: 0, covered: 0 });
        if (f.is_in_coverage) rec.covered += 1;
        else rec.newCount += 1;
      }
      setFindingsByRun(byRun);
    } catch (err) {
      const msg =
        typeof err === "object" && err && "body" in err
          ? ((err as { body?: { error?: { message?: string } } }).body?.error?.message ?? null)
          : null;
      setSystemError(err instanceof ApiError && err.status === 403 ? t("obs.usage.noPermission") : msg || t("obs.usage.errorSystem"));
    } finally {
      setLoadingSystem(false);
    }
  }, [t]);

  const loadByUser = useCallback(async (projectId: string) => {
    setByUser(null);
    try {
      const data = await api.get(
        `/admin/usage/by-user?projectId=${encodeURIComponent(projectId || "all")}`
      );
      // The endpoint now returns { users, period, totalUsers } with
      // camelCase aggregate names — map to the row shape this table
      // renders (older flat-array responses pass through unchanged).
      const rows: any[] = Array.isArray(data) ? data : ((data as any)?.users ?? []);
      setByUser(
        rows.map((u: any) => ({
          userId: u.userId,
          username: u.username ?? u.displayName ?? u.email ?? u.userId,
          email: u.email ?? "",
          projectName: u.projectName ?? "—",
          requests: u.requests ?? u.requestCount ?? 0,
          tokens: u.tokens ?? u.totalTokens ?? 0,
          cost: u.cost ?? u.totalCostUsd ?? 0,
        }))
      );
    } catch {
      setByUser([]);
    }
  }, []);

  useEffect(() => {
    loadUsage(ALL_PROJECTS);
    loadByUser(ALL_PROJECTS);
  }, [loadUsage, loadByUser]);

  useEffect(() => {
    if (tab === "system" && systemSummary === null && !loadingSystem) {
      loadSystem();
    }
  }, [tab, systemSummary, loadingSystem, loadSystem]);

  function handleSelect(projectId: string) {
    setSelectedProject(projectId);
    loadUsage(projectId);
    loadByUser(projectId);
  }

  // Chart feeds. Donut shows the top 4 models by cost; the tail is
  // folded into a gray "Other" slice so the legend stays readable.
  const barItems = byProject.data ?? [];
  const modelItems = byModel.data ?? [];
  const donutTop = [...modelItems].sort((a, b) => b.cost - a.cost).slice(0, 4);
  const donutTotal = modelItems.reduce((s, m) => s + m.cost, 0);
  const otherCost = donutTotal - donutTop.reduce((s, m) => s + m.cost, 0);
  const donutSegs = [
    ...donutTop.map((m) => ({ label: m.model, cost: m.cost })),
    ...(otherCost > 1e-9 ? [{ label: t("obs.usage.chart.other"), cost: otherCost }] : []),
  ];
  const donutColors = [...DONUT_PALETTE, "#9CA3AF"].slice(0, donutSegs.length);
  const maxUserTokens = Math.max(...(byUser ?? []).map((u) => u.tokens), 1);

  // --- AI review (system) tab derivations --------------------------------
  const runsWithFindings = systemRuns.filter((r) => r.findings_count > 0).length;
  const cleanRuns = systemRuns.length - runsWithFindings;
  const fetchedFindingsTotal = systemRuns.reduce((s, r) => s + r.findings_count, 0);
  const detectionRate = systemRuns.length > 0 ? (runsWithFindings / systemRuns.length) * 100 : 0;

  // Runs-per-day buckets for the last 14 days (local dates).
  const activityDays = Array.from({ length: 14 }, (_, i) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - (13 - i));
    return d;
  });
  const activityLabels = activityDays.map((d) =>
    d.toLocaleDateString(dateLocale, { month: "short", day: "numeric" })
  );
  const activityCounts = activityDays.map(
    (d) =>
      systemRuns.filter((r) => {
        if (!r.created_at) return false;
        const t = new Date(r.created_at);
        return (
          t.getFullYear() === d.getFullYear() &&
          t.getMonth() === d.getMonth() &&
          t.getDate() === d.getDate()
        );
      }).length
  );

  const triggerRows = [
    { id: "realtime", label: t("obs.usage.trigger.realtime"), count: systemSummary?.realtime_runs ?? 0, color: "#3B82F6" },
    { id: "manual", label: t("obs.usage.trigger.manual"), count: systemSummary?.manual_runs ?? 0, color: "#8B5CF6" },
  ];
  const maxTriggerCount = Math.max(...triggerRows.map((t) => t.count), 1);

  const filteredRuns = systemRuns
    .filter((r) =>
      runFilter === "findings"
        ? r.findings_count > 0
        : runFilter === "realtime"
          ? r.trigger === "realtime"
          : runFilter === "manual"
            ? r.trigger === "manual"
            : true
    )
    .slice(0, 20);

  return (
    <>
      <PageHeader
        title={t("obs.usage.title")}
        subtitle={t("obs.usage.subtitle")}
      />

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--color-border-default)", marginBottom: "1.5rem" }}>
        {([
          ["user", "obs.usage.tab.user"],
          ["system", "obs.usage.tab.system"],
        ] as [Tab, DictKey][]).map(([key, label]) => {
          const active = tab === key;
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              style={{
                padding: "0.75rem 1.25rem",
                background: "none",
                border: "none",
                borderBottom: active ? "2px solid var(--color-status-info)" : "2px solid transparent",
                fontWeight: active ? 600 : 400,
                fontSize: "var(--font-size-sm)",
                color: active ? "var(--color-text-primary)" : "var(--color-text-muted)",
                cursor: "pointer",
              }}
            >
              {t(label)}
            </button>
          );
        })}
      </div>

      {tab === "user" ? (
        <>
          {/* Scope pills — replaces the old SCOPE dropdown card. */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 20 }}>
            {[
              { id: ALL_PROJECTS, name: t("obs.usage.allProjects") },
              ...(projects.data?.items.map((p) => ({ id: p.project_id, name: p.name })) ?? []),
            ].map((p) => {
              const active = selectedProject === p.id;
              return (
                <button
                  key={p.id || "_all"}
                  onClick={() => handleSelect(p.id)}
                  style={{
                    padding: "4px 12px",
                    borderRadius: 12,
                    fontSize: 11,
                    fontWeight: active ? 600 : 500,
                    border: `1px solid ${active ? "transparent" : "var(--border-color)"}`,
                    cursor: "pointer",
                    fontFamily: "inherit",
                    background: active ? "#1E3A5F" : "var(--bg-card)",
                    color: active ? "#fff" : "var(--text-secondary)",
                    transition: "background 120ms, color 120ms",
                  }}
                >
                  {p.name}
                </button>
              );
            })}
          </div>

          {/* Stat cards */}
          {usageError ? (
            <div className="sran-card" style={{ marginBottom: 12 }}>
              <ErrorState
                message={usageError}
                onRetry={() => loadUsage(selectedProject)}
              />
            </div>
          ) : loadingUsage || !usage ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 12 }}>
              {[1, 2, 3].map((i) => (
                <div key={i} className="usage-stat-card">
                  <div className="sran-skeleton" style={{ width: "40%", height: "0.75rem", marginBottom: "0.75rem" }} />
                  <div className="sran-skeleton" style={{ width: "60%", height: "1.5rem" }} />
                </div>
              ))}
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 12 }}>
              <UsageStat
                accent="#3B82F6"
                label={t("obs.usage.stat.requests")}
                value={usage.total_requests.toLocaleString()}
                sub={
                  usage.total_requests > 0
                    ? t("obs.usage.stat.requestsSub", { n: Math.round(usage.total_tokens / usage.total_requests) })
                    : t("obs.usage.stat.noRequests")
                }
              />
              <UsageStat
                accent="#8B5CF6"
                label={t("obs.usage.stat.tokens")}
                value={formatTokensShort(usage.total_tokens)}
                sub={t("obs.usage.stat.tokensSub", { n: usage.total_tokens.toLocaleString() })}
              />
              <UsageStat
                accent="#10B981"
                label={t("obs.usage.stat.cost")}
                value={formatCost(usage.total_estimated_cost_usd)}
                sub={t("obs.usage.stat.costSub")}
              />
            </div>
          )}

          {/* Charts */}
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12, marginBottom: 12 }}>
            <div style={panelCard}>
              <div style={{ ...panelTitle, marginBottom: 10 }}>{t("obs.usage.chart.byProject")}</div>
              {byProject.loading ? (
                <div className="sran-skeleton" style={{ width: "100%", height: 160 }} />
              ) : barItems.length === 0 ? (
                <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "3rem 0", textAlign: "center" }}>
                  {t("obs.usage.chart.noRequestData")}
                </div>
              ) : (
                <div style={{ height: 160 }}>
                  <Bar
                    data={{
                      labels: barItems.map((p) =>
                        p.projectName.length > 14 ? `${p.projectName.slice(0, 13)}…` : p.projectName
                      ),
                      datasets: [
                        {
                          data: barItems.map((p) => p.requests),
                          backgroundColor: barItems.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
                          borderRadius: 3,
                          maxBarThickness: 36,
                        },
                      ],
                    }}
                    options={{
                      responsive: true,
                      maintainAspectRatio: false,
                      plugins: { legend: { display: false } },
                      scales: {
                        x: {
                          grid: { display: false },
                          ticks: { font: { size: 9 }, color: tickColor },
                        },
                        y: {
                          beginAtZero: true,
                          ticks: { font: { size: 9 }, color: tickColor },
                          grid: { color: gridColor },
                        },
                      },
                    }}
                  />
                </div>
              )}
            </div>

            <div style={panelCard}>
              <div style={{ ...panelTitle, marginBottom: 10 }}>{t("obs.usage.chart.costByModel")}</div>
              {byModel.loading ? (
                <div className="sran-skeleton" style={{ width: "100%", height: 100 }} />
              ) : donutSegs.length === 0 ? (
                <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "2.5rem 0", textAlign: "center" }}>
                  {t("obs.usage.chart.noCostData")}
                </div>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <div style={{ position: "relative", width: 100, height: 100, flexShrink: 0 }}>
                    <Doughnut
                      data={{
                        labels: donutSegs.map((s) => s.label),
                        datasets: [
                          {
                            data: donutSegs.map((s) => s.cost),
                            backgroundColor: donutColors,
                            borderWidth: 0,
                          },
                        ],
                      }}
                      options={{
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
                        plugins: { legend: { display: false } },
                      }}
                    />
                    <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        pointerEvents: "none",
                      }}
                    >
                      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-text-primary)" }}>
                        {formatCost(donutTotal)}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0, flex: 1 }}>
                    {donutSegs.map((s, i) => (
                      <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
                        <span style={{ width: 8, height: 8, borderRadius: "50%", background: donutColors[i], flexShrink: 0 }} />
                        <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {s.label}
                        </span>
                        <span style={{ fontWeight: 600, color: "var(--color-text-primary)", marginLeft: "auto", paddingLeft: 8 }}>
                          {formatCost(s.cost)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* User breakdown table */}
          <div style={{ ...panelCard, padding: 0 }}>
            <div style={{ padding: "14px 16px 0" }}>
              <div style={panelTitle}>{t("obs.usage.byUser")}</div>
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>{t("obs.usage.byUserSub")}</div>
            </div>
            {byUser === null ? (
              <div style={{ padding: "14px 16px" }}>
                <div className="sran-skeleton" style={{ width: "100%", height: "6rem" }} />
              </div>
            ) : byUser.length === 0 ? (
              <div style={{ padding: "14px 16px", fontSize: 11, color: "var(--text-muted)" }}>
                {t("obs.usage.byUserEmpty")}
              </div>
            ) : (
              <div style={{ overflowX: "auto", marginTop: 10 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, color: "var(--text-primary)" }}>
                  <thead>
                    <tr>
                      {([
                        "field.user",
                        "field.project",
                        "obs.usage.col.requests",
                        "obs.usage.col.tokens",
                        "obs.usage.col.usage",
                        "obs.usage.col.cost",
                      ] as DictKey[]).map((h) => (
                        <th
                          key={h}
                          style={{
                            textAlign: h === "obs.usage.col.cost" ? "right" : "left",
                            padding: "6px 16px",
                            fontSize: 9,
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                            color: "var(--text-secondary)",
                            fontWeight: 600,
                            background: "var(--bg-elevated)",
                            borderBottom: "1px solid var(--border-color)",
                          }}
                        >
                          {t(h)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {byUser.map((u, i) => (
                      <tr key={u.userId} style={{ borderBottom: "1px solid var(--border-color)" }}>
                        <td style={{ padding: "8px 16px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span
                              style={{
                                width: 22,
                                height: 22,
                                borderRadius: "50%",
                                background: CHART_PALETTE[i % CHART_PALETTE.length],
                                color: "#fff",
                                fontSize: 9,
                                fontWeight: 600,
                                display: "inline-flex",
                                alignItems: "center",
                                justifyContent: "center",
                                flexShrink: 0,
                              }}
                            >
                              {userInitials(u.username)}
                            </span>
                            <span style={{ minWidth: 0 }}>
                              <span style={{ display: "block", fontWeight: 500, color: "var(--color-text-primary)" }}>
                                {u.username}
                              </span>
                              <span style={{ display: "block", fontSize: 10, color: "var(--text-muted)" }}>{u.email}</span>
                            </span>
                          </div>
                        </td>
                        <td style={{ padding: "8px 16px", color: "var(--text-secondary)" }}>{u.projectName}</td>
                        <td style={{ padding: "8px 16px", fontVariantNumeric: "tabular-nums" }}>
                          {u.requests.toLocaleString()}
                        </td>
                        <td style={{ padding: "8px 16px", fontVariantNumeric: "tabular-nums" }}>
                          {formatTokensShort(u.tokens)}
                        </td>
                        <td style={{ padding: "8px 16px" }}>
                          <div style={{ width: 60, height: 6, borderRadius: 3, background: "var(--bg-subtle)", overflow: "hidden" }}>
                            <div
                              style={{
                                width: `${Math.round((u.tokens / maxUserTokens) * 100)}%`,
                                height: "100%",
                                borderRadius: 3,
                                background: CHART_PALETTE[i % CHART_PALETTE.length],
                              }}
                            />
                          </div>
                        </td>
                        <td style={{ padding: "8px 16px", fontWeight: 600, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {formatCost(u.cost)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          {systemError ? (
            <div className="sran-card">
              <ErrorState message={systemError} onRetry={loadSystem} />
            </div>
          ) : loadingSystem || !systemSummary ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="usage-stat-card" style={{ padding: "12px 14px" }}>
                  <div className="sran-skeleton" style={{ width: "40%", height: "0.75rem", marginBottom: "0.75rem" }} />
                  <div className="sran-skeleton" style={{ width: "60%", height: "1.5rem" }} />
                </div>
              ))}
            </div>
          ) : (
            <>
              {/* Stat cards */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 10 }}>
                <UsageStat
                  compact
                  accent="#3B82F6"
                  label={t("obs.usage.sys.stat.runs")}
                  value={systemSummary.total_runs.toLocaleString()}
                  sub={t("obs.usage.sys.stat.runsSub", { manual: systemSummary.manual_runs, realtime: systemSummary.realtime_runs })}
                />
                <UsageStat
                  compact
                  accent="#8B5CF6"
                  label={t("obs.usage.sys.stat.findings")}
                  value={(findingsSummary?.total_findings ?? fetchedFindingsTotal).toLocaleString()}
                  sub={t("obs.usage.sys.stat.findingsSub", {
                    fresh: findingsSummary?.new_patterns ?? 0,
                    covered: findingsSummary?.already_covered ?? 0,
                  })}
                />
                <UsageStat
                  compact
                  accent="#10B981"
                  label={t("obs.usage.sys.stat.cost")}
                  value={formatCost(systemSummary.total_cost_usd)}
                  sub={t("obs.usage.sys.stat.costSub", { cost: formatCost(systemSummary.avg_cost_per_run) })}
                />
                <UsageStat
                  compact
                  accent="#F59E0B"
                  label={t("obs.usage.sys.stat.detection")}
                  value={`${detectionRate.toFixed(1)}%`}
                  sub={t("obs.usage.sys.stat.detectionSub", { findings: fetchedFindingsTotal, runs: systemRuns.length })}
                />
              </div>

              {/* Charts */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
                <div style={{ ...panelCard, padding: "12px 14px" }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
                    {t("obs.usage.sys.activity")}
                  </div>
                  <div style={{ height: 120 }}>
                    <Bar
                      data={{
                        labels: activityLabels,
                        datasets: [
                          {
                            data: activityCounts,
                            backgroundColor: "#3B82F6",
                            borderRadius: 2,
                            maxBarThickness: 18,
                          },
                        ],
                      }}
                      options={{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                          x: {
                            grid: { display: false },
                            ticks: { font: { size: 8 }, color: tickColor, maxRotation: 0, autoSkip: true },
                          },
                          y: {
                            beginAtZero: true,
                            ticks: { font: { size: 8 }, color: tickColor, precision: 0 },
                            grid: { color: gridColor },
                          },
                        },
                      }}
                    />
                  </div>
                </div>

                <div
                  style={{
                    ...panelCard,
                    padding: "12px 14px",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    gap: 12,
                  }}
                >
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
                      {t("obs.usage.sys.byTrigger")}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {triggerRows.map((row) => (
                        <div key={row.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ width: 55, fontSize: 10, color: "var(--text-secondary)", flexShrink: 0 }}>{row.label}</span>
                          <div style={{ flex: 1, height: 14, borderRadius: 3, background: "var(--bg-subtle)", overflow: "hidden" }}>
                            <div
                              style={{
                                width: `${Math.round((row.count / maxTriggerCount) * 100)}%`,
                                height: "100%",
                                borderRadius: 3,
                                background: row.color,
                              }}
                            />
                          </div>
                          <span style={{ fontSize: 10, fontWeight: 600, minWidth: "3ch", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                            {row.count.toLocaleString()}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
                      {t("obs.usage.sys.findingsBreakdown")}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
                      {[
                        { id: "new", color: "#EF4444", label: t("obs.usage.sys.newPatterns"), count: findingsSummary?.new_patterns ?? 0 },
                        { id: "covered", color: "#F59E0B", label: t("obs.usage.sys.alreadyCovered"), count: findingsSummary?.already_covered ?? 0 },
                        { id: "clean", color: "#10B981", label: t("obs.usage.sys.noFindings"), count: cleanRuns },
                      ].map((it) => (
                        <div key={it.id} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: "var(--text-secondary)" }}>
                          <span style={{ width: 7, height: 7, borderRadius: "50%", background: it.color, flexShrink: 0 }} />
                          {it.label}
                          <span style={{ fontWeight: 700, color: "var(--color-text-primary)" }}>{it.count.toLocaleString()}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Recent runs table */}
              <div style={{ ...panelCard, padding: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "12px 14px 0", gap: 8, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>{t("obs.usage.sys.recentRuns")}</div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 2 }}>{t("obs.usage.sys.recentRunsSub")}</div>
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    {([
                      ["all", "field.all"],
                      ["findings", "obs.usage.sys.filter.withFindings"],
                      ["realtime", "obs.usage.trigger.realtime"],
                      ["manual", "obs.usage.trigger.manual"],
                    ] as [RunFilter, DictKey][]).map(([key, label]) => {
                      const active = runFilter === key;
                      return (
                        <button
                          key={key}
                          onClick={() => setRunFilter(key)}
                          style={{
                            padding: "3px 8px",
                            borderRadius: 10,
                            fontSize: 10,
                            fontWeight: active ? 600 : 500,
                            border: "none",
                            cursor: "pointer",
                            fontFamily: "inherit",
                            background: active ? "#1E3A5F" : "var(--bg-subtle)",
                            color: active ? "#fff" : "var(--text-secondary)",
                          }}
                        >
                          {t(label)}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {filteredRuns.length === 0 ? (
                  <div style={{ padding: "2rem 1rem" }}>
                    <EmptyState message={t("obs.usage.sys.noRuns")} icon={<Icon name="info" />} />
                  </div>
                ) : (
                  <div style={{ overflowX: "auto", marginTop: 8 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, color: "var(--text-primary)" }}>
                      <thead>
                        <tr>
                          {([
                            "obs.usage.col.time",
                            "obs.usage.col.trigger",
                            "field.model",
                            "obs.usage.col.findings",
                            "obs.usage.col.tokens",
                            "obs.usage.col.cost",
                            "field.status",
                          ] as DictKey[]).map((h) => (
                            <th
                              key={h}
                              style={{
                                textAlign: h === "obs.usage.col.cost" ? "right" : "left",
                                padding: "6px 14px",
                                fontSize: 9,
                                textTransform: "uppercase",
                                letterSpacing: "0.06em",
                                color: "var(--text-secondary)",
                                fontWeight: 600,
                                background: "var(--bg-elevated)",
                                borderBottom: "1px solid var(--border-color)",
                              }}
                            >
                              {t(h)}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {filteredRuns.map((r) => {
                          const split = findingsByRun[r.scan_run_id];
                          return (
                            <tr key={r.scan_run_id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                              <td style={{ padding: "8px 14px", whiteSpace: "nowrap" }}>
                                {r.created_at ? formatRunTime(r.created_at, dateLocale) : "—"}
                              </td>
                              <td style={{ padding: "8px 14px" }}>
                                <span
                                  style={{
                                    padding: "2px 8px",
                                    borderRadius: 10,
                                    fontSize: 9,
                                    fontWeight: 600,
                                    background: r.trigger === "realtime" ? "#FEF3CD" : "#E8F4FD",
                                    color: r.trigger === "realtime" ? "#92600A" : "#1565C0",
                                  }}
                                >
                                  {r.trigger}
                                </span>
                              </td>
                              <td style={{ padding: "8px 14px", fontFamily: "var(--font-mono, monospace)", fontSize: 10, color: "var(--text-secondary)" }}>
                                {shortModel(r.model)}
                              </td>
                              <td style={{ padding: "8px 14px" }}>
                                {r.findings_count === 0 ? (
                                  <span style={{ color: "var(--text-muted)" }}>0</span>
                                ) : (
                                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                                    <span
                                      style={{
                                        width: 6,
                                        height: 6,
                                        borderRadius: "50%",
                                        background: split && split.newCount === 0 ? "#F59E0B" : "#EF4444",
                                        flexShrink: 0,
                                      }}
                                    />
                                    {split
                                      ? split.newCount > 0
                                        ? t("obs.usage.sys.findingsNew", { n: split.newCount })
                                        : t("obs.usage.sys.findingsCovered", { n: split.covered })
                                      : t("obs.usage.sys.findingsFound", { n: r.findings_count })}
                                  </span>
                                )}
                              </td>
                              <td style={{ padding: "8px 14px", fontVariantNumeric: "tabular-nums" }}>
                                {r.total_tokens.toLocaleString()}
                              </td>
                              <td style={{ padding: "8px 14px", fontWeight: 600, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                                {formatCost(r.estimated_cost_usd)}
                              </td>
                              <td style={{ padding: "8px 14px" }}>
                                <span
                                  style={{
                                    padding: "2px 8px",
                                    borderRadius: 10,
                                    fontSize: 9,
                                    fontWeight: 600,
                                    background: r.status === "completed" ? "#ECFDF5" : r.status === "failed" ? "#FEF2F2" : "#FEF3CD",
                                    color: r.status === "completed" ? "#059669" : r.status === "failed" ? "#DC2626" : "#92600A",
                                  }}
                                >
                                  {r.status}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div style={{ marginTop: "1rem", padding: "0.75rem 1rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", fontStyle: "italic" }}>
                {t("obs.usage.sys.note")}
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
