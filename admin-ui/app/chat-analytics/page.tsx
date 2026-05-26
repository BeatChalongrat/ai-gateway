"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import PageHeader from "../../components/ui/PageHeader";
import StatCard from "../../components/ui/StatCard";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";

// Lightweight inline skeletons — the admin-ui Skeleton module only
// ships table + cards helpers, but we need a stat-card placeholder
// and a chart placeholder. Both wrap the .sran-skeleton class.
function SkeletonStat() {
  return (
    <div className="sran-card" style={{ padding: "1rem 1.25rem" }}>
      <div className="sran-skeleton" style={{ width: "40%", height: 12, marginBottom: 8 }} />
      <div className="sran-skeleton" style={{ width: "60%", height: 22, marginBottom: 6 }} />
      <div className="sran-skeleton" style={{ width: "30%", height: 10 }} />
    </div>
  );
}
function SkeletonChart() {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: "100%", padding: "0.5rem 0" }}>
      {Array.from({ length: 12 }).map((_, i) => (
        <div
          key={i}
          className="sran-skeleton"
          style={{ flex: 1, height: `${30 + ((i * 37) % 60)}%`, borderRadius: 4 }}
        />
      ))}
    </div>
  );
}
import {
  AnalyticsPeriod,
  AnalyticsResponse,
  AdminConversationListResponse,
  fetchAdminConversations,
  fetchAnalytics,
} from "../../lib/chatEdgeApi";

const PERIODS: { value: AnalyticsPeriod; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 Days" },
  { value: "30d", label: "Last 30 Days" },
  { value: "90d", label: "Last 90 Days" },
];

// Brand colors keep the by-model chart legible across providers.
// recharts fills don't resolve CSS variables so the hex stays inline.
const MODEL_COLOR: Record<string, string> = {
  openai: "#6366f1",
  anthropic: "#d97757",
  google: "#4285f4",
  meta: "#1877f2",
  mistral: "#ff7000",
};

function colorForModel(modelId: string): string {
  const id = modelId.toLowerCase();
  if (id.includes("gpt") || id.startsWith("o1") || id.startsWith("o3"))
    return MODEL_COLOR.openai;
  if (
    id.includes("claude") ||
    id.includes("sonnet") ||
    id.includes("opus") ||
    id.includes("haiku")
  )
    return MODEL_COLOR.anthropic;
  if (id.includes("gemini")) return MODEL_COLOR.google;
  if (id.includes("llama") || id.includes("meta")) return MODEL_COLOR.meta;
  if (id.includes("mistral") || id.includes("mixtral"))
    return MODEL_COLOR.mistral;
  return "#64748b";
}

function compactNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export default function ChatAnalyticsPage() {
  const [period, setPeriod] = useState<AnalyticsPeriod>("30d");
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [recent, setRecent] = useState<AdminConversationListResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (p: AnalyticsPeriod) => {
    setLoading(true);
    setError(null);
    try {
      const [analytics, recentList] = await Promise.all([
        fetchAnalytics(p),
        fetchAdminConversations({ limit: 10 }),
      ]);
      setData(analytics);
      setRecent(recentList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
      setData(null);
      setRecent(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(period);
  }, [period, load]);

  const byModel = useMemo(() => {
    if (!data) return [];
    return data.by_model.map((m) => ({
      ...m,
      color: colorForModel(m.model_id),
    }));
  }, [data]);

  const periodSelector = (
    <select
      value={period}
      onChange={(e) => setPeriod(e.target.value as AnalyticsPeriod)}
      className="sran-select"
      style={{
        padding: "0.4rem 0.65rem",
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: 6,
        color: "var(--color-text-primary)",
        fontSize: "0.8rem",
      }}
    >
      {PERIODS.map((p) => (
        <option key={p.value} value={p.value}>
          {p.label}
        </option>
      ))}
    </select>
  );

  return (
    <div>
      <PageHeader
        title="Chat Analytics"
        subtitle="Web Chat usage, model breakdown, and recent conversations"
        actions={periodSelector}
      />

      {error ? (
        <ErrorState message={error} onRetry={() => void load(period)} />
      ) : (
        <>
          {/* KPI cards */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "0.875rem",
              marginBottom: "1rem",
            }}
          >
            {loading || !data ? (
              <>
                <SkeletonStat />
                <SkeletonStat />
                <SkeletonStat />
                <SkeletonStat />
              </>
            ) : (
              <>
                <StatCard
                  label="Conversations"
                  value={compactNumber(data.totals.conversations)}
                  subtitle={`${data.totals.messages.toLocaleString()} messages`}
                />
                <StatCard
                  label="Active Users"
                  value={compactNumber(data.totals.active_users)}
                  subtitle={`${data.totals.files_uploaded.toLocaleString()} files uploaded`}
                />
                <StatCard
                  label="Tokens Used"
                  value={compactNumber(data.totals.tokens_used)}
                  subtitle="input + output"
                />
                <StatCard
                  label="Est. Cost"
                  value={formatCost(data.totals.estimated_cost_usd)}
                  subtitle="provider list pricing"
                />
              </>
            )}
          </div>

          {/* Charts */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
              gap: "0.875rem",
              marginBottom: "1rem",
            }}
            className="sran-chart-grid"
          >
            <div className="sran-card" style={{ padding: "1rem 1.125rem" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  justifyContent: "space-between",
                  marginBottom: "0.625rem",
                }}
              >
                <h3
                  style={{
                    fontSize: "0.85rem",
                    fontWeight: 600,
                    color: "var(--color-text-primary)",
                    margin: 0,
                  }}
                >
                  Messages Over Time
                </h3>
                <span style={{ fontSize: "0.7rem", color: "var(--color-text-muted)" }}>
                  daily
                </span>
              </div>
              <div style={{ height: 240 }}>
                {loading || !data ? (
                  <SkeletonChart />
                ) : data.daily.length === 0 ? (
                  <EmptyState message="No chat data yet" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={data.daily}
                      margin={{ top: 6, right: 8, left: -16, bottom: 0 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(120,140,180,0.12)"
                      />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: "rgba(180,200,230,0.7)" }}
                        tickFormatter={(v: string) => v.slice(5)}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: "rgba(180,200,230,0.7)" }}
                        tickFormatter={(v: number) => compactNumber(v)}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "var(--color-surface)",
                          border: "1px solid var(--color-border)",
                          fontSize: 12,
                          color: "var(--color-text-primary)",
                        }}
                        labelStyle={{ color: "var(--color-text-secondary)" }}
                      />
                      <Line
                        type="monotone"
                        dataKey="messages"
                        stroke="#3b82f6"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            <div className="sran-card" style={{ padding: "1rem 1.125rem" }}>
              <h3
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  margin: "0 0 0.625rem",
                }}
              >
                Usage by Model
              </h3>
              <div style={{ height: 240 }}>
                {loading || !data ? (
                  <SkeletonChart />
                ) : byModel.length === 0 ? (
                  <EmptyState message="No model usage yet" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={byModel}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 4, bottom: 0 }}
                    >
                      <CartesianGrid
                        strokeDasharray="3 3"
                        stroke="rgba(120,140,180,0.12)"
                        horizontal={false}
                      />
                      <XAxis
                        type="number"
                        tick={{ fontSize: 10, fill: "rgba(180,200,230,0.7)" }}
                        tickFormatter={(v: number) => compactNumber(v)}
                      />
                      <YAxis
                        type="category"
                        dataKey="model_id"
                        tick={{ fontSize: 10, fill: "rgba(180,200,230,0.8)" }}
                        width={120}
                        tickFormatter={(v: string) =>
                          v.length > 18 ? `${v.slice(0, 18)}…` : v
                        }
                      />
                      <Tooltip
                        contentStyle={{
                          background: "var(--color-surface)",
                          border: "1px solid var(--color-border)",
                          fontSize: 12,
                          color: "var(--color-text-primary)",
                        }}
                        labelStyle={{ color: "var(--color-text-secondary)" }}
                      />
                      <Bar dataKey="messages" radius={[0, 4, 4, 0]}>
                        {byModel.map((m, i) => (
                          <Cell key={i} fill={m.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>

          {/* Recent conversations */}
          <div className="sran-card" style={{ padding: "1rem 1.125rem" }}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: "0.625rem",
              }}
            >
              <h3
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  margin: 0,
                }}
              >
                Recent Conversations
              </h3>
              <Link
                href="/chat-conversations"
                style={{
                  fontSize: "0.7rem",
                  color: "var(--color-sran-500, #60a5fa)",
                }}
              >
                View all →
              </Link>
            </div>
            {loading || !recent ? (
              <SkeletonTable rows={6} cols={6} />
            ) : recent.conversations.length === 0 ? (
              <EmptyState message="No conversations yet" />
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 720 }}>
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>User</th>
                      <th>Model</th>
                      <th style={{ textAlign: "right" }}>Messages</th>
                      <th style={{ textAlign: "right" }}>Tokens</th>
                      <th>Last Active</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.conversations.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <Link
                            href={`/chat-conversations/${c.id}`}
                            style={{
                              color: "var(--color-text-primary)",
                              fontWeight: 500,
                            }}
                          >
                            {c.title || "Untitled"}
                          </Link>
                        </td>
                        <td style={{ color: "var(--color-text-secondary)" }}>
                          {c.user_email || "—"}
                        </td>
                        <td style={{ color: "var(--color-text-secondary)" }}>
                          {c.model_id}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {c.message_count}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {compactNumber(c.token_count)}
                        </td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatRelative(c.updated_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
