"use client";

import PageHeader from "../components/ui/PageHeader";
import Badge from "../components/ui/Badge";
import { SkeletonTable } from "../components/ui/Skeleton";
import ErrorState from "../components/ui/ErrorState";
import { useApi } from "../lib/useApi";

interface HealthMap {
  gateway_api: string;
  dlp_engine: string;
  routing_engine: string;
  provider_adapters: string;
}

interface DashboardData {
  tenants: number;
  projects: number;
  models: number;
  providers: number;
  health: HealthMap;
  recent_requests: {
    request_id: string;
    provider: string;
    model: string;
    status: string;
    latency_ms: number;
  }[];
}

/* ── KPI metric tile ────────────────────────────────────────────── */

function Kpi({ label, value, detail, href }: {
  label: string;
  value: string | number;
  detail: string;
  href?: string;
}) {
  const content = (
    <div style={{
      display: "flex", flexDirection: "column",
      padding: "1.125rem 1.25rem 1rem",
      position: "relative",
    }}>
      {/* Left accent */}
      <div style={{
        position: "absolute", left: 0, top: "12%", bottom: "12%", width: 3,
        borderRadius: "0 2px 2px 0",
        background: "var(--color-sran-500)",
        opacity: 0.7,
      }} />
      <span style={{
        fontSize: "0.6875rem", fontWeight: 600, textTransform: "uppercase",
        letterSpacing: "0.05em", color: "var(--color-text-secondary)",
        lineHeight: 1,
      }}>{label}</span>
      <span style={{
        fontSize: "1.875rem", fontWeight: 700, letterSpacing: "-0.035em",
        lineHeight: 1.15, color: "var(--color-text-primary)",
        fontVariantNumeric: "tabular-nums",
        marginTop: "0.5rem",
      }}>{value}</span>
      <span style={{
        fontSize: "0.75rem", color: "var(--color-text-muted)",
        marginTop: "0.3125rem", lineHeight: 1.3,
      }}>{detail}</span>
    </div>
  );

  const cardStyle: React.CSSProperties = {
    background: "var(--color-surface-primary)",
    border: "1px solid var(--color-border-default)",
    borderRadius: "var(--radius-lg)",
    boxShadow: "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)",
    overflow: "hidden",
    textDecoration: "none",
    color: "inherit",
    transition: "box-shadow 150ms, border-color 150ms",
  };

  if (href) {
    return (
      <a href={href} style={{ ...cardStyle, display: "block" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.boxShadow = "0 4px 12px -2px rgba(15, 31, 56, 0.10), 0 1px 3px rgba(15, 31, 56, 0.06)";
          e.currentTarget.style.borderColor = "var(--color-border-strong)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.boxShadow = "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)";
          e.currentTarget.style.borderColor = "var(--color-border-default)";
        }}>
        {content}
      </a>
    );
  }
  return <div style={cardStyle}>{content}</div>;
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function HomePage() {
  const { data, loading, error, refetch } = useApi<DashboardData>("/admin/dashboard");

  const tenantCount = data?.tenants ?? "—";
  const projectCount = data?.projects ?? "—";
  const modelCount = data?.models ?? "—";
  const providerCount = data?.providers ?? "—";
  const recentRequests = data?.recent_requests ?? [];

  const healthMap = data?.health;
  const healthItems = [
    { name: "Gateway API",       key: "gateway_api" as const },
    { name: "DLP Engine",        key: "dlp_engine" as const },
    { name: "Routing Engine",    key: "routing_engine" as const },
    { name: "Provider Adapters", key: "provider_adapters" as const },
  ];
  const allHealthy = healthMap !== undefined && healthItems.every((h) => healthMap[h.key] === "ok");
  const anyDown = healthMap !== undefined && healthItems.some((h) => healthMap[h.key] !== "ok");

  return (
    <>
      <PageHeader title="Dashboard" subtitle="SRAN AI Gateway — platform overview" />

      {/* ── KPI row ── */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
        gap: "0.875rem", marginBottom: "1.5rem",
      }}>
        <Kpi label="Tenants" value={tenantCount} detail="Active organizations" href="/tenants" />
        <Kpi label="Projects" value={projectCount} detail="Across all tenants" href="/projects" />
        <Kpi label="Models" value={modelCount} detail={`${providerCount} provider${providerCount !== 1 ? "s" : ""} configured`} href="/models" />
        <Kpi label="Requests" value={recentRequests.length} detail="Recent activity" href="/requests" />
      </div>

      {/* ── Platform Health ── */}
      <div style={{
        background: "var(--color-surface-primary)",
        border: "1px solid var(--color-border-default)",
        borderRadius: "var(--radius-lg)",
        boxShadow: "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)",
        padding: "1rem 1.5rem",
        marginBottom: "1.5rem",
      }}>
        {/* Header row */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: "0.875rem",
        }}>
          <h3 style={{
            fontSize: "0.9375rem", fontWeight: 600, margin: 0,
            color: "var(--color-text-primary)",
          }}>Platform Health</h3>
          <Badge variant={allHealthy ? "success" : loading || !healthMap ? "neutral" : "warning"}>
            {allHealthy
              ? "All Systems Operational"
              : loading || !healthMap
                ? "Checking..."
                : anyDown ? "Degraded" : "Unknown"}
          </Badge>
        </div>

        {/* Component status grid */}
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "0.625rem",
        }}>
          {healthItems.map((h) => {
            const ok = healthMap ? healthMap[h.key] === "ok" : false;
            return (
              <div key={h.key} style={{
                display: "flex", alignItems: "center", gap: "0.5rem",
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                background: ok ? "rgba(5, 150, 105, 0.04)" : "var(--color-surface-tertiary)",
                border: `1px solid ${ok ? "rgba(5, 150, 105, 0.12)" : "var(--color-border-default)"}`,
              }}>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                  background: ok ? "#10b981" : "#cbd5e1",
                  boxShadow: ok ? "0 0 0 2.5px rgba(16, 185, 129, 0.15)" : "none",
                }} />
                <span style={{
                  fontSize: "0.8125rem", fontWeight: 500,
                  color: ok ? "var(--color-text-primary)" : "var(--color-text-muted)",
                }}>{h.name}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Recent Requests ── */}
      <div style={{
        background: "var(--color-surface-primary)",
        border: "1px solid var(--color-border-default)",
        borderRadius: "var(--radius-lg)",
        boxShadow: "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)",
        overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "0.875rem 1.5rem",
          borderBottom: "1px solid var(--color-border-default)",
        }}>
          <h3 style={{
            fontSize: "0.9375rem", fontWeight: 600, margin: 0,
            color: "var(--color-text-primary)",
          }}>Recent Requests</h3>
          {recentRequests.length > 0 ? (
            <a href="/requests" style={{
              fontSize: "0.8125rem", color: "var(--color-text-accent)",
              textDecoration: "none", fontWeight: 500,
            }}>View all &rarr;</a>
          ) : (
            <span style={{
              fontSize: "0.75rem", color: "var(--color-text-secondary)",
            }}>Live request feed</span>
          )}
        </div>

        {/* Body */}
        {loading ? (
          <SkeletonTable rows={5} cols={5} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : recentRequests.length > 0 ? (
          <div style={{ overflowX: "auto" }}>
            <table className="sran-table">
              <thead>
                <tr>
                  <th>Request ID</th>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Latency</th>
                </tr>
              </thead>
              <tbody>
                {recentRequests.map((r) => (
                  <tr key={r.request_id}>
                    <td><code style={{ fontSize: "var(--font-size-xs)" }}>{r.request_id.slice(0, 16)}...</code></td>
                    <td style={{ textTransform: "capitalize" }}>{r.provider}</td>
                    <td><code style={{ fontSize: "var(--font-size-xs)" }}>{r.model}</code></td>
                    <td><Badge variant={r.status === "success" ? "success" : "error"}>{r.status}</Badge></td>
                    <td style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{r.latency_ms}ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          /* Empty state */
          <div style={{
            display: "flex", flexDirection: "column", alignItems: "center",
            padding: "2.25rem 1.5rem 2.5rem",
          }}>
            <div style={{
              width: 44, height: 44, marginBottom: "1rem",
              borderRadius: "var(--radius-lg)",
              background: "var(--color-surface-tertiary)",
              border: "1px solid var(--color-border-default)",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--color-text-secondary)",
            }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
              </svg>
            </div>
            <span style={{
              fontSize: "0.875rem", fontWeight: 600,
              color: "var(--color-text-primary)", marginBottom: "0.25rem",
            }}>No requests yet</span>
            <span style={{
              fontSize: "0.8125rem", color: "var(--color-text-secondary)",
              lineHeight: 1.55, textAlign: "center", maxWidth: "24rem",
            }}>
              Send a request to{" "}
              <code style={{
                fontSize: "0.75rem", padding: "0.0625rem 0.375rem",
                background: "var(--color-surface-tertiary)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border-default)",
              }}>/v1/chat/completions</code>{" "}
              to see activity here.
            </span>
          </div>
        )}
      </div>
    </>
  );
}
