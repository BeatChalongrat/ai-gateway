"use client";

import { useMemo, useState } from "react";
import PageHeader from "../../../components/ui/PageHeader";
import Badge from "../../../components/ui/Badge";
import DataTable, { Column } from "../../../components/ui/DataTable";
import { Icon } from "../../../components/ui/Icon";
import Modal from "../../../components/ui/Modal";
import { useApi } from "../../../lib/useApi";

// ---------------------------------------------------------------------------
// Types — derived from backend AlertEventResponse (schemas/admin.py:233).
// Note: backend does NOT return `webhook_url`; the field is kept optional
// here so the page tolerates a future addition without churn.
// ---------------------------------------------------------------------------

interface AlertEvent {
  id: string;
  rule_id: string | null;
  alert_type: string;
  severity: string;
  message: string;
  delivery_status: string;
  fired_at: string | null;
  tenant_id: string | null;
  project_id: string | null;
  webhook_url?: string | null;
}

interface AlertEventListResponse {
  items: AlertEvent[];
}

function severityBadge(severity: string) {
  const variant =
    severity === "critical" ? "error" :
    severity === "warning" ? "warning" :
    severity === "info" ? "info" :
    "neutral";
  return <Badge variant={variant}>{severity}</Badge>;
}

function deliveryBadge(status: string) {
  const variant =
    status === "delivered" ? "success" :
    status === "failed" ? "error" :
    status === "pending" ? "warning" :
    status === "log_only" ? "neutral" :
    "neutral";
  return <Badge variant={variant}>{status}</Badge>;
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

// Small token-colored filter pill (mirrors the audit-log quick-chip style).
function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "4px 11px",
        borderRadius: 100,
        fontSize: "var(--font-size-xs)",
        fontWeight: active ? 600 : 500,
        border: `1px solid ${active ? "var(--color-status-info)" : "var(--color-border-default)"}`,
        background: active ? "var(--color-status-info-bg)" : "var(--color-surface-primary)",
        color: active ? "var(--color-status-info)" : "var(--color-text-secondary)",
        cursor: "pointer",
        fontFamily: "inherit",
        textTransform: "capitalize",
        transition: "background 120ms, border-color 120ms",
      }}
    >
      {children}
    </button>
  );
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

const columns: Column<AlertEvent>[] = [
  {
    key: "fired_at",
    header: "Fired at",
    render: (r) => (
      <span style={{ fontSize: "var(--font-size-sm)", whiteSpace: "nowrap" }}>{formatDateTime(r.fired_at)}</span>
    ),
  },
  { key: "alert_type", header: "Alert type", render: (r) => <code style={{ fontSize: "var(--font-size-xs)" }}>{r.alert_type}</code> },
  { key: "severity", header: "Severity", render: (r) => severityBadge(r.severity) },
  {
    key: "message",
    header: "Message",
    render: (r) => (
      <span style={{ fontSize: "var(--font-size-sm)" }}>{truncate(r.message, 60)}</span>
    ),
  },
  { key: "delivery_status", header: "Delivery", render: (r) => deliveryBadge(r.delivery_status) },
  {
    key: "tenant_id",
    header: "Tenant",
    render: (r) =>
      r.tenant_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{r.tenant_id}</code> : "—",
  },
];

export default function AlertEventsPage() {
  const { data, loading, error, refetch } = useApi<AlertEventListResponse>("/admin/alert-events?limit=50");
  const [selected, setSelected] = useState<AlertEvent | null>(null);
  const [sevFilter, setSevFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const items = data?.items ?? null;
  const severities = useMemo(
    () => Array.from(new Set((items ?? []).map((i) => i.severity))).sort(),
    [items],
  );
  const statuses = useMemo(
    () => Array.from(new Set((items ?? []).map((i) => i.delivery_status))).sort(),
    [items],
  );
  const filtered = useMemo(() => {
    if (!items) return null;
    return items.filter(
      (i) =>
        (!sevFilter || i.severity === sevFilter) &&
        (!statusFilter || i.delivery_status === statusFilter),
    );
  }, [items, sevFilter, statusFilter]);

  return (
    <>
      <PageHeader
        title="Alert events"
        subtitle="Recent fired alert events across all rules"
        action={
          <button className="sran-btn sran-btn-secondary" onClick={refetch}>
            Refresh
          </button>
        }
      />
      {items && items.length > 0 && (severities.length > 1 || statuses.length > 1) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1.25rem", marginBottom: "0.875rem" }}>
          {severities.length > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginRight: "0.25rem" }}>Severity</span>
              <FilterPill active={sevFilter === ""} onClick={() => setSevFilter("")}>All</FilterPill>
              {severities.map((s) => (
                <FilterPill key={s} active={sevFilter === s} onClick={() => setSevFilter(sevFilter === s ? "" : s)}>{s}</FilterPill>
              ))}
            </div>
          )}
          {statuses.length > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginRight: "0.25rem" }}>Delivery</span>
              <FilterPill active={statusFilter === ""} onClick={() => setStatusFilter("")}>All</FilterPill>
              {statuses.map((s) => (
                <FilterPill key={s} active={statusFilter === s} onClick={() => setStatusFilter(statusFilter === s ? "" : s)}>{s}</FilterPill>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        <DataTable
          columns={columns}
          data={filtered}
          loading={loading}
          error={error}
          emptyMessage="No alert events recorded yet"
          emptyIcon={<Icon name="bell" size={22} />}
          rowKey={(r) => r.id}
          onRowClick={(row) => setSelected(row)}
          searchable
          searchPlaceholder="Search by type, severity, message..."
          onRetry={refetch}
        />
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title="Alert event detail"
        footer={
          <button className="sran-btn sran-btn-secondary" onClick={() => setSelected(null)}>Close</button>
        }
      >
        {selected && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", fontSize: "var(--font-size-sm)" }}>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Event ID</div>
              <div className="sran-detail-value"><code style={{ fontSize: "var(--font-size-xs)" }}>{selected.id}</code></div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Rule ID</div>
              <div className="sran-detail-value">
                {selected.rule_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.rule_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Alert type</div>
              <div className="sran-detail-value"><code>{selected.alert_type}</code></div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Severity</div>
              <div className="sran-detail-value">{severityBadge(selected.severity)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Delivery</div>
              <div className="sran-detail-value">{deliveryBadge(selected.delivery_status)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Fired at</div>
              <div className="sran-detail-value">{formatDateTime(selected.fired_at)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Tenant</div>
              <div className="sran-detail-value">
                {selected.tenant_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.tenant_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">Project</div>
              <div className="sran-detail-value">
                {selected.project_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.project_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field" style={{ gridColumn: "1 / -1" }}>
              <div className="sran-detail-label">Webhook URL</div>
              <div className="sran-detail-value">
                {selected.webhook_url ? (
                  <code style={{ fontSize: "var(--font-size-xs)", wordBreak: "break-all" }}>{selected.webhook_url}</code>
                ) : (
                  "—"
                )}
              </div>
            </div>
            <div className="sran-detail-field" style={{ gridColumn: "1 / -1" }}>
              <div className="sran-detail-label">Message</div>
              <div className="sran-detail-value" style={{ whiteSpace: "pre-wrap" }}>{selected.message}</div>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
