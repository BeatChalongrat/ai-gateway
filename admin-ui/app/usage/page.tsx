"use client";

import { useCallback, useEffect, useState } from "react";
import PageHeader from "../../components/ui/PageHeader";
import StatCard from "../../components/ui/StatCard";
import Badge from "../../components/ui/Badge";
import ErrorState from "../../components/ui/ErrorState";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";

interface ProjectItem {
  project_id: string;
  name: string;
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

const ALL_PROJECTS = "";

type Tab = "user" | "system";

function formatCost(n: number | null | undefined): string {
  if (n == null || n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(6)}`;
  return `$${n.toFixed(4)}`;
}

export default function UsagePage() {
  const projects = useApi<{ items: ProjectItem[] }>("/admin/projects");
  const [tab, setTab] = useState<Tab>("user");

  const [selectedProject, setSelectedProject] = useState<string>(ALL_PROJECTS);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [usageError, setUsageError] = useState<string | null>(null);

  const [systemSummary, setSystemSummary] = useState<ScanRunsSummary | null>(null);
  const [systemRuns, setSystemRuns] = useState<ScanRun[]>([]);
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
      setUsageError(msg || "Failed to load usage data");
      setUsage(null);
    } finally {
      setLoadingUsage(false);
    }
  }, []);

  const loadSystem = useCallback(async () => {
    setSystemError(null);
    setLoadingSystem(true);
    try {
      const [summary, runs] = await Promise.all([
        api.get("/admin/ai-review/scan-runs/summary"),
        api.get("/admin/ai-review/scan-runs?limit=20"),
      ]);
      setSystemSummary(summary as ScanRunsSummary);
      setSystemRuns(((runs as ScanRunsResponse).items) ?? []);
    } catch (err) {
      const msg =
        typeof err === "object" && err && "body" in err
          ? ((err as { body?: { error?: { message?: string } } }).body?.error?.message ?? null)
          : null;
      setSystemError(msg || "Failed to load AI review cost data");
    } finally {
      setLoadingSystem(false);
    }
  }, []);

  useEffect(() => {
    loadUsage(ALL_PROJECTS);
  }, [loadUsage]);

  useEffect(() => {
    if (tab === "system" && systemSummary === null && !loadingSystem) {
      loadSystem();
    }
  }, [tab, systemSummary, loadingSystem, loadSystem]);

  function handleSelect(projectId: string) {
    setSelectedProject(projectId);
    loadUsage(projectId);
  }

  const scopeLabel = selectedProject
    ? projects.data?.items.find((p) => p.project_id === selectedProject)?.name ?? "Project"
    : "All projects";

  return (
    <>
      <PageHeader
        title="Usage & Cost"
        subtitle="Token consumption and cost for user traffic and the AI review system"
      />

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--color-border-default)", marginBottom: "1.5rem" }}>
        {([
          ["user", "User traffic"],
          ["system", "AI review (system)"],
        ] as [Tab, string][]).map(([key, label]) => {
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
              {label}
            </button>
          );
        })}
      </div>

      {tab === "user" ? (
        <>
          <div className="sran-card" style={{ marginBottom: "1.5rem" }}>
            <label className="sran-label">Scope</label>
            <select
              className="sran-input"
              style={{ maxWidth: "24rem" }}
              value={selectedProject}
              onChange={(e) => handleSelect(e.target.value)}
            >
              <option value={ALL_PROJECTS}>All projects</option>
              {projects.data?.items.map((p) => (
                <option key={p.project_id} value={p.project_id}>{p.name}</option>
              ))}
            </select>
            <div
              style={{
                marginTop: "0.5rem",
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-muted)",
              }}
            >
              Showing totals for: <strong>{scopeLabel}</strong>
            </div>
          </div>

          {usageError ? (
            <div className="sran-card">
              <ErrorState
                message={usageError}
                onRetry={() => loadUsage(selectedProject)}
              />
            </div>
          ) : loadingUsage || !usage ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem" }}>
              {[1, 2, 3].map((i) => (
                <div key={i} className="sran-card">
                  <div className="sran-skeleton" style={{ width: "40%", height: "0.75rem", marginBottom: "0.75rem" }} />
                  <div className="sran-skeleton" style={{ width: "60%", height: "1.5rem" }} />
                </div>
              ))}
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem" }}>
              <StatCard
                label="Total Requests"
                value={usage.total_requests.toLocaleString()}
                trend={usage.total_requests > 0 ? "active" : undefined}
              />
              <StatCard
                label="Total Tokens"
                value={usage.total_tokens.toLocaleString()}
                trend={usage.total_tokens > 0 ? "active" : undefined}
              />
              <StatCard
                label="Estimated Cost"
                value={`$${usage.total_estimated_cost_usd.toFixed(4)}`}
                subtitle="Based on per-model rates"
              />
            </div>
          )}
        </>
      ) : (
        <>
          {systemError ? (
            <div className="sran-card">
              <ErrorState message={systemError} onRetry={loadSystem} />
            </div>
          ) : loadingSystem || !systemSummary ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem" }}>
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="sran-card">
                  <div className="sran-skeleton" style={{ width: "40%", height: "0.75rem", marginBottom: "0.75rem" }} />
                  <div className="sran-skeleton" style={{ width: "60%", height: "1.5rem" }} />
                </div>
              ))}
            </div>
          ) : (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem", marginBottom: "1.5rem" }}>
                <StatCard label="Total runs" value={systemSummary.total_runs.toLocaleString()} subtitle={`${systemSummary.manual_runs} manual · ${systemSummary.realtime_runs} realtime`} />
                <StatCard label="Total tokens" value={systemSummary.total_tokens.toLocaleString()} />
                <StatCard label="Total cost" value={formatCost(systemSummary.total_cost_usd)} />
                <StatCard label="Avg cost per run" value={formatCost(systemSummary.avg_cost_per_run)} />
              </div>

              <div className="sran-card" style={{ padding: 0 }}>
                {systemRuns.length === 0 ? (
                  <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-muted)" }}>
                    No scan runs yet.
                  </div>
                ) : (
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", fontSize: "var(--font-size-xs)", borderCollapse: "collapse" }}>
                      <thead>
                        <tr>
                          {["Time", "Trigger", "Provider", "Model", "Requests", "Findings", "Tokens", "Cost", "Status"].map((h) => (
                            <th key={h} style={{ textAlign: "left", padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)", fontWeight: 600 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {systemRuns.map((r) => (
                          <tr key={r.scan_run_id}>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>
                              {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                            </td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>
                              <Badge variant={r.trigger === "realtime" ? "warning" : "info"}>{r.trigger}</Badge>
                            </td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>{r.provider}</td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}><code>{r.model}</code></td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>{r.requests_scanned}</td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>{r.findings_count}</td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>{r.total_tokens.toLocaleString()}</td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>{formatCost(r.estimated_cost_usd)}</td>
                            <td style={{ padding: "0.5rem 0.75rem", borderBottom: "1px solid var(--color-border-default)" }}>
                              <Badge variant={r.status === "completed" ? "success" : r.status === "failed" ? "error" : "warning"}>{r.status}</Badge>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              <div style={{ marginTop: "1rem", padding: "0.75rem 1rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", fontStyle: "italic" }}>
                These costs are incurred by the gateway itself for AI review scans, separate from user/tenant traffic costs.
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
