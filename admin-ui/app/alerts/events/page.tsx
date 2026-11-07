"use client";

import { useMemo, useState } from "react";
import PageHeader from "../../../components/ui/PageHeader";
import Badge from "../../../components/ui/Badge";
import DataTable, { Column } from "../../../components/ui/DataTable";
import { Icon } from "../../../components/ui/Icon";
import Modal from "../../../components/ui/Modal";
import { useApi } from "../../../lib/useApi";
import { useT } from "../../../lib/i18n";
import { ALERT_DELIVERY, ALERT_SEVERITY, codeWord } from "../../../lib/codeWords";

type Translate = ReturnType<typeof useT>;

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

// AM11: the badge says the word for the stored code (lib/codeWords.ts); the
// code itself stays what the page filters and searches by.
function severityBadge(severity: string, t: Translate) {
  const variant =
    severity === "critical" ? "error" :
    severity === "warning" ? "warning" :
    severity === "info" ? "info" :
    "neutral";
  return <Badge variant={variant}>{codeWord(ALERT_SEVERITY, severity, t)}</Badge>;
}

function deliveryBadge(status: string, t: Translate) {
  const variant =
    status === "delivered" ? "success" :
    status === "failed" ? "error" :
    status === "pending" ? "warning" :
    "neutral";
  return <Badge variant={variant}>{codeWord(ALERT_DELIVERY, status, t)}</Badge>;
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

// Column headers are translated. An alert's type and the tenant id stay
// verbatim — identifiers an operator quotes in a ticket. Its severity and
// what became of it are states, and read as words (AM11).
function useColumns(): Column<AlertEvent>[] {
  const t = useT();
  return useMemo(
    () => [
      {
        key: "fired_at",
        header: t("obs.alerts.col.firedAt"),
        render: (r) => (
          <span style={{ fontSize: "var(--font-size-sm)", whiteSpace: "nowrap" }}>{formatDateTime(r.fired_at)}</span>
        ),
      },
      { key: "alert_type", header: t("obs.alerts.col.alertType"), render: (r) => <code style={{ fontSize: "var(--font-size-xs)" }}>{r.alert_type}</code> },
      { key: "severity", header: t("obs.alerts.col.severity"), render: (r) => severityBadge(r.severity, t) },
      {
        key: "message",
        header: t("obs.alerts.col.message"),
        render: (r) => (
          <span style={{ fontSize: "var(--font-size-sm)" }}>{truncate(r.message, 60)}</span>
        ),
      },
      { key: "delivery_status", header: t("obs.alerts.col.delivery"), render: (r) => deliveryBadge(r.delivery_status, t) },
      {
        key: "tenant_id",
        header: t("field.tenant"),
        render: (r) =>
          r.tenant_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{r.tenant_id}</code> : "—",
      },
    ],
    [t],
  );
}

export default function AlertEventsPage() {
  const t = useT();
  const columns = useColumns();
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
        title={t("obs.alerts.title")}
        subtitle={t("obs.alerts.subtitle")}
        action={
          <button className="sran-btn sran-btn-secondary" onClick={refetch}>
            {t("action.refresh")}
          </button>
        }
      />
      {items && items.length > 0 && (severities.length > 1 || statuses.length > 1) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1.25rem", marginBottom: "0.875rem" }}>
          {severities.length > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginRight: "0.25rem" }}>{t("obs.alerts.col.severity")}</span>
              <FilterPill active={sevFilter === ""} onClick={() => setSevFilter("")}>{t("field.all")}</FilterPill>
              {severities.map((s) => (
                <FilterPill key={s} active={sevFilter === s} onClick={() => setSevFilter(sevFilter === s ? "" : s)}>{codeWord(ALERT_SEVERITY, s, t)}</FilterPill>
              ))}
            </div>
          )}
          {statuses.length > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginRight: "0.25rem" }}>{t("obs.alerts.col.delivery")}</span>
              <FilterPill active={statusFilter === ""} onClick={() => setStatusFilter("")}>{t("field.all")}</FilterPill>
              {statuses.map((s) => (
                <FilterPill key={s} active={statusFilter === s} onClick={() => setStatusFilter(statusFilter === s ? "" : s)}>{codeWord(ALERT_DELIVERY, s, t)}</FilterPill>
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
          emptyMessage={t("obs.alerts.empty")}
          emptyIcon={<Icon name="bell" size={22} />}
          rowKey={(r) => r.id}
          onRowClick={(row) => setSelected(row)}
          searchable
          searchPlaceholder={t("obs.alerts.searchPlaceholder")}
          onRetry={refetch}
        />
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={t("obs.alerts.detailTitle")}
        footer={
          <button className="sran-btn sran-btn-secondary" onClick={() => setSelected(null)}>{t("action.close")}</button>
        }
      >
        {selected && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", fontSize: "var(--font-size-sm)" }}>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.detail.eventId")}</div>
              <div className="sran-detail-value"><code style={{ fontSize: "var(--font-size-xs)" }}>{selected.id}</code></div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.detail.ruleId")}</div>
              <div className="sran-detail-value">
                {selected.rule_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.rule_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.col.alertType")}</div>
              <div className="sran-detail-value"><code>{selected.alert_type}</code></div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.col.severity")}</div>
              <div className="sran-detail-value">{severityBadge(selected.severity, t)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.col.delivery")}</div>
              <div className="sran-detail-value">{deliveryBadge(selected.delivery_status, t)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("obs.alerts.col.firedAt")}</div>
              <div className="sran-detail-value">{formatDateTime(selected.fired_at)}</div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("field.tenant")}</div>
              <div className="sran-detail-value">
                {selected.tenant_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.tenant_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("field.project")}</div>
              <div className="sran-detail-value">
                {selected.project_id ? <code style={{ fontSize: "var(--font-size-xs)" }}>{selected.project_id}</code> : "—"}
              </div>
            </div>
            <div className="sran-detail-field" style={{ gridColumn: "1 / -1" }}>
              <div className="sran-detail-label">{t("obs.alerts.detail.webhookUrl")}</div>
              <div className="sran-detail-value">
                {selected.webhook_url ? (
                  <code style={{ fontSize: "var(--font-size-xs)", wordBreak: "break-all" }}>{selected.webhook_url}</code>
                ) : (
                  "—"
                )}
              </div>
            </div>
            <div className="sran-detail-field" style={{ gridColumn: "1 / -1" }}>
              <div className="sran-detail-label">{t("obs.alerts.col.message")}</div>
              <div className="sran-detail-value" style={{ whiteSpace: "pre-wrap" }}>{selected.message}</div>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
