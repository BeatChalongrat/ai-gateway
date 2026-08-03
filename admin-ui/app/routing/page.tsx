"use client";

import { useState, useMemo } from "react";
import PageHeader from "../../components/ui/PageHeader";
import Badge from "../../components/ui/Badge";
import DataTable, { Column } from "../../components/ui/DataTable";
import { Icon } from "../../components/ui/Icon";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { useApi } from "../../lib/useApi";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { useT, type DictKey } from "../../lib/i18n";

// ---------------------------------------------------------------------------
// Types — derived from backend schemas/admin.py
// ---------------------------------------------------------------------------

interface ModelStat {
  model: string;
  count: number;
}

interface RouterStat {
  router: string | null;
  count: number;
}

interface RoutingStats {
  models: ModelStat[];
  routers: RouterStat[];
}

type KeywordScope = "global" | "tenant" | "project";

interface KeywordRule {
  id: string;
  name: string;
  keywords: string[];
  model_override: string;
  priority: number;
  scope: KeywordScope;
  scope_id: string | null;
  enabled: boolean;
  created_at: string | null;
}

interface KeywordRuleListResponse {
  items: KeywordRule[];
}

interface KeywordRuleForm {
  name: string;
  keywords: string;
  model_override: string;
  priority: number;
  scope: KeywordScope;
  scope_id: string;
  enabled: boolean;
}

const EMPTY_FORM: KeywordRuleForm = {
  name: "",
  keywords: "",
  model_override: "",
  priority: 0,
  scope: "global",
  scope_id: "",
  enabled: true,
};

// Cycle palette for "By model" row dots — first row purple, second teal,
// then repeats. Kept short and explicit; the visual job here is "tell
// these apart at a glance", not encode a meaning.
const MODEL_DOT_COLORS = ["#534AB7", "#1D9E75"];

// Scope VALUES stay on the wire as global/tenant/project; only the label
// shown in the picker and in validation copy is localized.
const SCOPE_LABEL: Record<KeywordScope, DictKey> = {
  global: "infra.routing.scopeGlobal",
  tenant: "infra.routing.scopeTenant",
  project: "infra.routing.scopeProject",
};

function scopeBadgeVariant(scope: KeywordScope): "neutral" | "info" | "warning" {
  if (scope === "tenant") return "info";
  if (scope === "project") return "warning";
  return "neutral";
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function formToBody(form: KeywordRuleForm) {
  return {
    name: form.name.trim(),
    keywords: form.keywords
      .split("\n")
      .map((k) => k.trim())
      .filter((k) => k.length > 0),
    model_override: form.model_override.trim(),
    priority: Number(form.priority) || 0,
    scope: form.scope,
    scope_id: form.scope === "global" ? null : form.scope_id.trim() || null,
    enabled: form.enabled,
  };
}

function ruleToForm(rule: KeywordRule): KeywordRuleForm {
  return {
    name: rule.name,
    keywords: rule.keywords.join("\n"),
    model_override: rule.model_override,
    priority: rule.priority,
    scope: rule.scope,
    scope_id: rule.scope_id ?? "",
    enabled: rule.enabled,
  };
}

// ---------------------------------------------------------------------------
// Lucide-style SVG icons (inline to avoid extra deps)
// ---------------------------------------------------------------------------

function IconRotateCw({ spinning = false }: { spinning?: boolean }) {
  return (
    <svg
      width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={spinning ? { animation: "spin 1s linear infinite" } : undefined}
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

function IconPlus() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function IconTrash2() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}

// 22px area-line chart glyph for the stats empty state.
function IconChartArea() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v18h18" />
      <path d="M7 15l4-6 4 3 5-7" />
    </svg>
  );
}

// Stat-tile glyphs (16px). Stroke uses currentColor so the icon picks
// up the per-tile accent shade set on its container.
function IconChartBar() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="20" x2="12" y2="10" />
      <line x1="18" y1="20" x2="18" y2="4" />
      <line x1="6" y1="20" x2="6" y2="16" />
    </svg>
  );
}

function IconCpu() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="2" ry="2" />
      <rect x="9" y="9" width="6" height="6" />
      <line x1="9" y1="1" x2="9" y2="4" />
      <line x1="15" y1="1" x2="15" y2="4" />
      <line x1="9" y1="20" x2="9" y2="23" />
      <line x1="15" y1="20" x2="15" y2="23" />
      <line x1="20" y1="9" x2="23" y2="9" />
      <line x1="20" y1="14" x2="23" y2="14" />
      <line x1="1" y1="9" x2="4" y2="9" />
      <line x1="1" y1="14" x2="4" y2="14" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Stat sub-component — icon + badge on the top row, value + label below,
// with a 2px accent line across the top of the card (the ::before on
// .routing-stat-card in globals.css, tinted via --stat-accent).
// ---------------------------------------------------------------------------

function StatTile({
  icon,
  label,
  value,
  accentColor,
  chipClass,
  badgeText,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  accentColor: string;
  // chip-* class from globals.css — carries both the light pastel and
  // the dark semi-transparent fill for the icon square + corner badge.
  chipClass: string;
  badgeText: string;
  loading?: boolean;
}) {
  return (
    <div className="routing-stat-card" style={{ ["--stat-accent" as string]: accentColor }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 12,
        }}
      >
        <div
          className={chipClass}
          style={{
            width: 34,
            height: 34,
            borderRadius: 10,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {icon}
        </div>
        <span
          className={chipClass}
          style={{
            padding: "2px 8px",
            borderRadius: 6,
            fontSize: 10,
            fontWeight: 600,
          }}
        >
          {badgeText}
        </span>
      </div>
      {loading ? (
        <div className="sran-skeleton" style={{ width: "60%", height: "28px" }} />
      ) : (
        <div
          style={{
            fontSize: "28px",
            fontWeight: 700,
            color: "var(--color-text-primary)",
            lineHeight: 1.1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </div>
      )}
      <div
        style={{
          fontSize: "12px",
          color: "var(--color-text-muted)",
          marginTop: 6,
        }}
      >
        {label}
      </div>
    </div>
  );
}

// Centered empty-state block shared by the stats card and keyword rules.
function EmptyState({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        padding: "2.5rem 1.5rem",
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 12,
          background: "var(--bg-subtle)",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 12,
        }}
      >
        {icon}
      </div>
      <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-secondary)", margin: "0 0 4px" }}>{title}</p>
      <p style={{ fontSize: 11, color: "var(--text-muted)", margin: 0, maxWidth: 260, lineHeight: 1.5 }}>
        {description}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breakdown row sub-components
// ---------------------------------------------------------------------------

function ModelRow({
  model,
  count,
  totalRequests,
  index,
  isLast,
}: {
  model: string;
  count: number;
  totalRequests: number;
  index: number;
  isLast: boolean;
}) {
  const dotColor = MODEL_DOT_COLORS[index % MODEL_DOT_COLORS.length];
  const pct = totalRequests > 0 ? Math.round((count / totalRequests) * 100) : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.625rem 1.125rem",
        borderBottom: isLast ? "none" : "1px solid var(--color-border-subtle, rgba(255,255,255,0.05))",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: dotColor,
          flexShrink: 0,
        }}
      />
      <code
        style={{
          flex: 1,
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {model || "unknown"}
      </code>
      <span
        style={{
          fontSize: "11px",
          color: "var(--color-text-muted)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {pct}%
      </span>
      <span
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-primary)",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          minWidth: "2.5ch",
          textAlign: "right",
        }}
      >
        {count.toLocaleString()}
      </span>
    </div>
  );
}

function RouterRow({
  router,
  count,
  maxCount,
  isLast,
}: {
  router: string | null;
  count: number;
  maxCount: number;
  isLast: boolean;
}) {
  const barWidth = maxCount > 0 ? Math.max(2, Math.round((count / maxCount) * 80)) : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.625rem 1.125rem",
        borderBottom: isLast ? "none" : "1px solid var(--color-border-subtle, rgba(255,255,255,0.05))",
      }}
    >
      <span
        style={{
          flex: 1,
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-primary)",
          textTransform: router ? "capitalize" : "none",
        }}
      >
        {router ?? <Badge variant="neutral">—</Badge>}
      </span>
      <div
        style={{
          width: 80,
          height: 5,
          background: "var(--color-bg-surface-2)",
          opacity: 0.6,
          borderRadius: 3,
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: `${barWidth}px`,
            height: "100%",
            background: "var(--color-sran-600)",
            borderRadius: 3,
          }}
        />
      </div>
      <span
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-primary)",
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          minWidth: "2.5ch",
          textAlign: "right",
        }}
      >
        {count.toLocaleString()}
      </span>
    </div>
  );
}

// Section label ABOVE the breakdown card — 11px uppercase tracking.
function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "11px",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--color-text-muted)",
        marginBottom: "0.5rem",
      }}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Routing stats — read-only sub-component
// ---------------------------------------------------------------------------

function RoutingStatsSection({
  rules,
  rulesLoading,
  refetchRules,
}: {
  rules: KeywordRuleListResponse | null;
  rulesLoading: boolean;
  refetchRules: () => void;
}) {
  const t = useT();
  const { data, loading, error, refetch } = useApi<RoutingStats>("/admin/routing/stats");
  const [refreshing, setRefreshing] = useState(false);

  async function handleRefresh() {
    setRefreshing(true);
    await refetch();
    refetchRules();
    setRefreshing(false);
  }

  const totalRequests = useMemo(() => {
    if (!data) return 0;
    return data.models.reduce((sum, m) => sum + m.count, 0);
  }, [data]);

  const activeModels = useMemo(() => {
    if (!data) return 0;
    return data.models.filter((m) => m.count > 0).length;
  }, [data]);

  const maxRouterCount = useMemo(() => {
    if (!data || data.routers.length === 0) return 0;
    return Math.max(...data.routers.map((r) => r.count));
  }, [data]);

  const hasData = data && (data.models.length > 0 || data.routers.length > 0);

  return (
    <section style={{ marginBottom: 20 }}>
      {/* Title + Refresh share one row so they align exactly; the
          subtitle sits below the row instead of stretching it. */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
            {t("infra.routing.statsTitle")}
          </h2>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.375rem",
              background: "var(--bg-card)",
              border: "1px solid var(--border-color)",
              color: "var(--text-secondary)",
              borderRadius: 8,
              fontSize: 11,
              fontWeight: 600,
              padding: "6px 12px",
              cursor: refreshing ? "default" : "pointer",
              fontFamily: "inherit",
            }}
          >
            <IconRotateCw spinning={refreshing} />
            {t("action.refresh")}
          </button>
        </div>
        <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: "0.25rem 0 0" }}>
          {t("infra.routing.statsSubtitle")}
        </p>
      </div>

      {/* Stat tiles — icon + range badge on top, value + label below,
          top accent line per tile. */}
      <div className="routing-metrics-grid" style={{ marginBottom: 20 }}>
        <StatTile
          icon={<IconChartBar />}
          label={t("infra.routing.requests")}
          value={loading ? "—" : totalRequests.toLocaleString()}
          accentColor="#3B82F6"
          chipClass="chip-blue"
          badgeText={t("infra.routing.badge24h")}
          loading={loading}
        />
        <StatTile
          icon={<IconCpu />}
          label={t("infra.routing.activeModels")}
          value={loading ? "—" : activeModels.toString()}
          accentColor="#10B981"
          chipClass="chip-green"
          badgeText={t("field.active")}
          loading={loading}
        />
        <StatTile
          icon={<Icon name="key" size={16} />}
          label={t("infra.routing.keywordRules")}
          value={rulesLoading ? "—" : (rules?.items.length ?? 0).toLocaleString()}
          accentColor="#1E3A5F"
          chipClass="chip-navy"
          badgeText={t("infra.routing.badgeTotal")}
          loading={rulesLoading}
        />
      </div>

      {/* Request volume — single card: header bar (title + range pills)
          with the breakdown lists / empty state in the body. */}
      <div
        style={{
          background: "var(--color-bg-surface)",
          border: "0.5px solid var(--color-border-default)",
          borderRadius: 12,
        }}
      >
        <div
          style={{
            padding: "12px 20px",
            borderBottom: "1px solid var(--color-border-subtle, rgba(0,0,0,0.06))",
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{t("infra.routing.requestVolume")}</span>
        </div>

        <div style={{ padding: "18px 20px" }}>
          {loading ? (
            <div className="routing-charts-grid">
              <div>
                <SectionLabel>{t("infra.routing.byModel")}</SectionLabel>
                <div className="sran-skeleton" style={{ width: "100%", height: "8rem", borderRadius: 12 }} />
              </div>
              <div>
                <SectionLabel>{t("infra.routing.byRouter")}</SectionLabel>
                <div className="sran-skeleton" style={{ width: "100%", height: "8rem", borderRadius: 12 }} />
              </div>
            </div>
          ) : error ? (
            <div style={{ padding: "1.5rem", textAlign: "center" }}>
              <p style={{ color: "var(--color-status-error)", marginBottom: "0.75rem" }}>{error}</p>
              <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={handleRefresh}>{t("action.retry")}</button>
            </div>
          ) : !hasData ? (
            <EmptyState
              icon={<IconChartArea />}
              title={t("infra.routing.noTraffic")}
              description={t("infra.routing.noTrafficDesc")}
            />
          ) : (
            <div className="routing-charts-grid">
              <div>
                <SectionLabel>{t("infra.routing.byModel")}</SectionLabel>
                <div
                  style={{
                    padding: "0.25rem 0",
                    border: "1px solid var(--color-border-subtle, rgba(0,0,0,0.06))",
                    borderRadius: 12,
                  }}
                >
                  {(data?.models ?? []).length === 0 ? (
                    <div style={{ padding: "1rem 1.125rem", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>
                      {t("infra.routing.noModelData")}
                    </div>
                  ) : (
                    (data?.models ?? []).map((m, i, arr) => (
                      <ModelRow
                        key={m.model || `unknown-${i}`}
                        model={m.model}
                        count={m.count}
                        totalRequests={totalRequests}
                        index={i}
                        isLast={i === arr.length - 1}
                      />
                    ))
                  )}
                </div>
              </div>
              <div>
                <SectionLabel>{t("infra.routing.byRouter")}</SectionLabel>
                <div
                  style={{
                    padding: "0.25rem 0",
                    border: "1px solid var(--color-border-subtle, rgba(0,0,0,0.06))",
                    borderRadius: 12,
                  }}
                >
                  {(data?.routers ?? []).length === 0 ? (
                    <div style={{ padding: "1rem 1.125rem", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>
                      {t("infra.routing.noRouterData")}
                    </div>
                  ) : (
                    (data?.routers ?? []).map((r, i, arr) => (
                      <RouterRow
                        key={r.router ?? `_null-${i}`}
                        router={r.router}
                        count={r.count}
                        maxCount={maxRouterCount}
                        isLast={i === arr.length - 1}
                      />
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Keyword rules CRUD — sub-component
// ---------------------------------------------------------------------------

function KeywordRulesSection({
  data,
  loading,
  error,
  refetch,
}: {
  data: KeywordRuleListResponse | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}) {
  const { toast } = useToast();
  const t = useT();

  const [editing, setEditing] = useState<KeywordRule | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<KeywordRuleForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KeywordRule | null>(null);
  const [deleting, setDeleting] = useState(false);

  function openCreate() {
    setForm(EMPTY_FORM);
    setErrors({});
    setCreating(true);
  }

  function openEdit(rule: KeywordRule) {
    setForm(ruleToForm(rule));
    setErrors({});
    setEditing(rule);
  }

  function closeForm() {
    setCreating(false);
    setEditing(null);
    setErrors({});
  }

  function validate(): boolean {
    const e: Record<string, string> = {};
    if (!form.name.trim()) e.name = t("infra.routing.errName");
    const keywordList = form.keywords.split("\n").map((k) => k.trim()).filter((k) => k.length > 0);
    if (keywordList.length === 0) e.keywords = t("infra.routing.errKeywords");
    if (!form.model_override.trim()) e.model_override = t("infra.routing.errModel");
    if (form.scope !== "global" && !form.scope_id.trim()) {
      e.scope_id = t("infra.routing.errScopeId", { scope: t(SCOPE_LABEL[form.scope]) });
    }
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    try {
      const body = formToBody(form);
      if (editing) {
        await api.put(`/admin/keyword-rules/${editing.id}`, body);
        toast(t("infra.routing.toastUpdated"));
      } else {
        await api.post("/admin/keyword-rules", body);
        toast(t("infra.routing.toastCreated"));
      }
      closeForm();
      refetch();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("infra.routing.saveFailedStatus", { status: err.status })
          : err instanceof Error
            ? err.message
            : t("infra.routing.saveFailed");
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/keyword-rules/${deleteTarget.id}`);
      toast(t("infra.routing.toastDeleted"));
      setDeleteTarget(null);
      if (editing && editing.id === deleteTarget.id) {
        closeForm();
      }
      refetch();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("infra.routing.deleteFailedStatus", { status: err.status })
          : err instanceof Error
            ? err.message
            : t("infra.routing.deleteFailed");
      toast(msg, "error");
    } finally {
      setDeleting(false);
    }
  }

  const columns: Column<KeywordRule>[] = [
    {
      key: "name",
      header: t("infra.routing.colKeyword"),
      render: (r) => (
        <div>
          <span style={{ fontWeight: 500, color: "var(--color-text-primary)" }}>{r.name}</span>
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.125rem" }}>
            {truncate(r.keywords.join(", "), 50)}
          </div>
        </div>
      ),
    },
    {
      key: "model_override",
      header: t("infra.routing.colTargetModel"),
      render: (r) => (
        <code style={{ fontSize: "var(--font-size-xs)", padding: "0.125rem 0.375rem", borderRadius: "var(--radius-sm)", background: "var(--color-bg-surface-2)" }}>
          {r.model_override}
        </code>
      ),
    },
    {
      key: "priority",
      header: t("infra.routing.colPriority"),
      render: (r) => (
        <span style={{
          display: "inline-block", fontSize: "var(--font-size-xs)", fontWeight: 600,
          padding: "0.125rem 0.5rem", borderRadius: "9999px",
          background: r.priority >= 10 ? "var(--color-status-warning-bg)" : "var(--color-bg-surface-2)",
          color: r.priority >= 10 ? "var(--color-status-warning)" : "var(--color-text-secondary)",
        }}>
          {r.priority}
        </span>
      ),
    },
    {
      key: "enabled",
      header: t("field.status"),
      render: (r) => (
        <span style={{
          display: "inline-flex", alignItems: "center", gap: "0.375rem",
          fontSize: "var(--font-size-xs)", fontWeight: 500,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: r.enabled ? "var(--color-status-success)" : "var(--color-text-muted)",
          }} />
          {r.enabled ? t("field.active") : t("field.inactive")}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      width: "80px",
      render: (r) => (
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }} onClick={(e) => e.stopPropagation()}>
          <button
            className="sran-btn-icon"
            title={t("infra.routing.editRule")}
            onClick={() => openEdit(r)}
            style={{ color: "var(--color-text-muted)", cursor: "pointer", background: "none", border: "none", padding: "0.25rem" }}
          >
            <IconSettings />
          </button>
          <button
            className="sran-btn-icon"
            title={t("infra.routing.deleteRule")}
            onClick={() => setDeleteTarget(r)}
            style={{ color: "var(--color-status-error)", cursor: "pointer", background: "none", border: "none", padding: "0.25rem", opacity: 0.7 }}
          >
            <IconTrash2 />
          </button>
        </div>
      ),
    },
  ];

  const isFormOpen = creating || editing !== null;
  const hasRules = data?.items && data.items.length > 0;

  return (
    <section>
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: 13, fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
            {t("infra.routing.keywordRules")}
          </h2>
          <button
            className="sran-btn sran-btn-primary"
            onClick={openCreate}
            style={{ display: "inline-flex", alignItems: "center", gap: "0.375rem" }}
          >
            <IconPlus />
            {t("infra.routing.newRule")}
          </button>
        </div>
        <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: "0.25rem 0 0" }}>
          {t("infra.routing.rulesSubtitle")}
        </p>
      </div>

      {loading ? (
        <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "0.75rem 1.25rem", borderBottom: "1px solid var(--color-border-default)" }}>
            <div className="sran-skeleton" style={{ width: "200px", height: "2rem" }} />
          </div>
          <table className="sran-table">
            <thead>
              <tr>
                {/* Skeleton header cells — no text is rendered, so these are
                    plain column indices rather than (untranslated) labels. */}
                {[0, 1, 2, 3, 4].map((h) => (
                  <th key={h}><div className="sran-skeleton" style={{ width: "60%" }} /></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[1, 2, 3].map((i) => (
                <tr key={i}>
                  {[1, 2, 3, 4, 5].map((j) => (
                    <td key={j}><div className="sran-skeleton" style={{ width: ["70%", "50%", "30%", "40%", "20%"][j - 1] }} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : error ? (
        <div className="sran-card" style={{ padding: "2rem", textAlign: "center" }}>
          <p style={{ color: "var(--color-status-error)", marginBottom: "0.75rem" }}>{error}</p>
          <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={refetch}>{t("action.retry")}</button>
        </div>
      ) : !hasRules ? (
        <div
          style={{
            background: "var(--color-bg-surface)",
            border: "1px dashed var(--color-border-default)",
            borderRadius: 12,
            padding: "18px 20px",
          }}
        >
          <EmptyState
            icon={<Icon name="key" size={22} />}
            title={t("infra.routing.noRules")}
            description={t("infra.routing.noRulesDesc")}
          />
        </div>
      ) : (
        <div className="sran-card routing-metric-card" style={{ padding: 0, overflow: "hidden" }}>
          <DataTable
            columns={columns}
            data={data?.items ?? null}
            loading={loading}
            error={error}
            emptyMessage={t("infra.routing.emptyTable")}
            emptyIcon={<Icon name="git-merge" size={22} />}
            emptyAction={<button className="sran-btn sran-btn-primary" onClick={openCreate}>{t("infra.routing.createFirst")}</button>}
            rowKey={(r) => r.id}
            onRowClick={openEdit}
            searchable
            searchPlaceholder={t("infra.routing.searchRules")}
            onRetry={refetch}
          />
        </div>
      )}

      <Modal
        open={isFormOpen}
        onClose={closeForm}
        title={editing ? t("infra.routing.modalEdit") : t("infra.routing.modalNew")}
        footer={
          <>
            {editing && (
              <button
                className="sran-btn sran-btn-danger"
                onClick={() => setDeleteTarget(editing)}
                disabled={saving}
                style={{ marginRight: "auto" }}
              >
                {t("action.delete")}
              </button>
            )}
            <button className="sran-btn sran-btn-secondary" onClick={closeForm} disabled={saving}>{t("action.cancel")}</button>
            <button className="sran-btn sran-btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? t("action.saving") : t("action.save")}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {editing && (
            <div className="sran-detail-field">
              <div className="sran-detail-label">{t("infra.routing.ruleId")}</div>
              <div className="sran-detail-value"><code style={{ fontSize: "var(--font-size-xs)" }}>{editing.id}</code></div>
            </div>
          )}
          <div>
            <label className="sran-label">{t("infra.routing.fieldName")}</label>
            <input
              className={`sran-input ${errors.name ? "sran-input-error" : ""}`}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t("infra.routing.namePlaceholder")}
              maxLength={128}
            />
            {errors.name && <div className="sran-field-error">{errors.name}</div>}
          </div>
          <div>
            <label className="sran-label">{t("infra.routing.fieldKeywords")}</label>
            <textarea
              className={`sran-input ${errors.keywords ? "sran-input-error" : ""}`}
              value={form.keywords}
              onChange={(e) => setForm({ ...form, keywords: e.target.value })}
              placeholder={"กฎหมาย\nlegal\nlawsuit"}
              rows={5}
              style={{ fontFamily: "var(--font-mono, monospace)", resize: "vertical" }}
            />
            {errors.keywords && <div className="sran-field-error">{errors.keywords}</div>}
          </div>
          <div>
            <label className="sran-label">{t("infra.routing.fieldModelOverride")}</label>
            <input
              className={`sran-input ${errors.model_override ? "sran-input-error" : ""}`}
              value={form.model_override}
              onChange={(e) => setForm({ ...form, model_override: e.target.value })}
              placeholder="claude-3-5-sonnet"
            />
            {errors.model_override && <div className="sran-field-error">{errors.model_override}</div>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
            <div>
              <label className="sran-label">{t("infra.routing.fieldPriority")}</label>
              <input
                className="sran-input"
                type="number"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="sran-label">{t("infra.routing.fieldScope")}</label>
              <select
                className="sran-input"
                value={form.scope}
                onChange={(e) => setForm({ ...form, scope: e.target.value as KeywordScope })}
              >
                <option value="global">{t("infra.routing.scopeGlobal")}</option>
                <option value="tenant">{t("infra.routing.scopeTenant")}</option>
                <option value="project">{t("infra.routing.scopeProject")}</option>
              </select>
            </div>
          </div>
          {form.scope !== "global" && (
            <div>
              <label className="sran-label">{form.scope === "tenant" ? t("infra.routing.fieldTenantId") : t("infra.routing.fieldProjectId")}</label>
              <input
                className={`sran-input ${errors.scope_id ? "sran-input-error" : ""}`}
                value={form.scope_id}
                onChange={(e) => setForm({ ...form, scope_id: e.target.value })}
                placeholder={form.scope === "tenant" ? "demo-tenant-000" : "demo-project-000"}
              />
              {errors.scope_id && <div className="sran-field-error">{errors.scope_id}</div>}
            </div>
          )}
          <div>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              <span className="sran-label" style={{ margin: 0 }}>{t("field.enabled")}</span>
            </label>
          </div>
        </div>
      </Modal>

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title={t("infra.routing.confirmDeleteTitle")}
        message={t("infra.routing.confirmDeleteMsg", { name: deleteTarget?.name ?? "" })}
        confirmLabel={t("infra.routing.deleteRule")}
        loading={deleting}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RoutingPage() {
  const t = useT();
  // Keyword rules are fetched once here so the "Keyword rules" stat tile
  // and the rules table below always show the same list.
  const rulesApi = useApi<KeywordRuleListResponse>("/admin/keyword-rules");
  return (
    <>
      <PageHeader title={t("infra.routing.title")} subtitle={t("infra.routing.subtitle")} />
      <RoutingStatsSection
        rules={rulesApi.data}
        rulesLoading={rulesApi.loading}
        refetchRules={rulesApi.refetch}
      />
      <KeywordRulesSection
        data={rulesApi.data}
        loading={rulesApi.loading}
        error={rulesApi.error}
        refetch={rulesApi.refetch}
      />
    </>
  );
}
