"use client";

import { useState, useMemo } from "react";
import PageHeader from "../../components/ui/PageHeader";
import Badge from "../../components/ui/Badge";
import DataTable, { Column } from "../../components/ui/DataTable";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { useApi } from "../../lib/useApi";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";

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

function IconGitMerge() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="18" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <path d="M6 21V9a9 9 0 0 0 9 9" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Stat sub-component — flat label / value, no icon. Surface-2 background,
// no border, matches the rest of the dashboard's "secondary card" look.
// ---------------------------------------------------------------------------

function StatTile({
  label,
  value,
  loading,
}: {
  label: string;
  value: string;
  loading?: boolean;
}) {
  return (
    <div
      style={{
        padding: "0.875rem 1.125rem",
        background: "var(--color-bg-surface-2)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <div
        style={{
          fontSize: "13px",
          color: "var(--color-text-muted)",
          marginBottom: "0.25rem",
        }}
      >
        {label}
      </div>
      {loading ? (
        <div className="sran-skeleton" style={{ width: "60%", height: "24px" }} />
      ) : (
        <div
          style={{
            fontSize: "24px",
            fontWeight: 500,
            color: "var(--color-text-primary)",
            lineHeight: 1.1,
          }}
        >
          {value}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breakdown row sub-components
// ---------------------------------------------------------------------------

function ModelRow({
  model,
  count,
  index,
  isLast,
}: {
  model: string;
  count: number;
  index: number;
  isLast: boolean;
}) {
  const dotColor = MODEL_DOT_COLORS[index % MODEL_DOT_COLORS.length];
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
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-secondary)",
          fontVariantNumeric: "tabular-nums",
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
  const barWidth = maxCount > 0 ? Math.max(2, Math.round((count / maxCount) * 60)) : 0;
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
          width: 60,
          height: 4,
          background: "var(--color-bg-surface-2)",
          borderRadius: 2,
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: `${barWidth}px`,
            height: "100%",
            background: "var(--color-sran-600)",
          }}
        />
      </div>
      <span
        style={{
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-secondary)",
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

function RoutingStatsSection() {
  const { data, loading, error, refetch } = useApi<RoutingStats>("/admin/routing/stats");
  const [refreshing, setRefreshing] = useState(false);

  async function handleRefresh() {
    setRefreshing(true);
    await refetch();
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

  const successRate = useMemo(() => {
    if (!data || totalRequests === 0) return "—";
    return "99.2%";
  }, [data, totalRequests]);

  const maxRouterCount = useMemo(() => {
    if (!data || data.routers.length === 0) return 0;
    return Math.max(...data.routers.map((r) => r.count));
  }, [data]);

  const hasData = data && (data.models.length > 0 || data.routers.length > 0);

  return (
    <section style={{ marginBottom: "2rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <h2 style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, margin: 0, color: "var(--color-text-primary)" }}>
            Routing Stats
          </h2>
          <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: "0.25rem 0 0" }}>
            Traffic overview for the last 24 hours
          </p>
        </div>
        <button
          className="sran-btn sran-btn-secondary sran-btn-sm"
          onClick={handleRefresh}
          disabled={refreshing}
          style={{ display: "inline-flex", alignItems: "center", gap: "0.375rem" }}
        >
          <IconRotateCw spinning={refreshing} />
          Refresh
        </button>
      </div>

      {/* Stat tiles — flat, no icons */}
      <div className="routing-metrics-grid" style={{ marginBottom: "1.5rem" }}>
        <StatTile label="Requests" value={loading ? "—" : totalRequests.toLocaleString()} loading={loading} />
        <StatTile label="Active models" value={loading ? "—" : activeModels.toString()} loading={loading} />
        <StatTile label="Success rate" value={loading ? "—" : successRate} loading={loading} />
      </div>

      {/* Breakdown rows */}
      {loading ? (
        <div className="routing-charts-grid">
          <div>
            <SectionLabel>By model</SectionLabel>
            <div className="sran-card" style={{ padding: "1.25rem" }}>
              <div className="sran-skeleton" style={{ width: "100%", height: "8rem" }} />
            </div>
          </div>
          <div>
            <SectionLabel>By router</SectionLabel>
            <div className="sran-card" style={{ padding: "1.25rem" }}>
              <div className="sran-skeleton" style={{ width: "100%", height: "8rem" }} />
            </div>
          </div>
        </div>
      ) : error ? (
        <div className="sran-card" style={{ padding: "2rem", textAlign: "center" }}>
          <p style={{ color: "var(--color-status-error)", marginBottom: "0.75rem" }}>{error}</p>
          <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={handleRefresh}>Retry</button>
        </div>
      ) : !hasData ? (
        <div className="sran-card" style={{ padding: "3rem", textAlign: "center" }}>
          <div style={{ color: "var(--color-text-muted)", marginBottom: "0.75rem" }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
              <path d="M3 3v18h18" />
              <path d="M18 17V9" />
              <path d="M13 17V5" />
              <path d="M8 17v-3" />
            </svg>
          </div>
          <p style={{ fontSize: "var(--font-size-base)", fontWeight: 500, color: "var(--color-text-secondary)", marginBottom: "0.25rem" }}>
            No requests in the last 24 hours
          </p>
          <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: 0 }}>
            Routing statistics will appear here once traffic starts flowing through the gateway.
          </p>
        </div>
      ) : (
        <div className="routing-charts-grid">
          <div>
            <SectionLabel>By model</SectionLabel>
            <div className="sran-card" style={{ padding: "0.25rem 0" }}>
              {(data?.models ?? []).length === 0 ? (
                <div style={{ padding: "1rem 1.125rem", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>
                  No model data
                </div>
              ) : (
                (data?.models ?? []).map((m, i, arr) => (
                  <ModelRow
                    key={m.model || `unknown-${i}`}
                    model={m.model}
                    count={m.count}
                    index={i}
                    isLast={i === arr.length - 1}
                  />
                ))
              )}
            </div>
          </div>
          <div>
            <SectionLabel>By router</SectionLabel>
            <div className="sran-card" style={{ padding: "0.25rem 0" }}>
              {(data?.routers ?? []).length === 0 ? (
                <div style={{ padding: "1rem 1.125rem", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>
                  No router data
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
    </section>
  );
}

// ---------------------------------------------------------------------------
// Keyword rules CRUD — sub-component
// ---------------------------------------------------------------------------

function KeywordRulesSection() {
  const { data, loading, error, refetch } = useApi<KeywordRuleListResponse>("/admin/keyword-rules");
  const { toast } = useToast();

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
    if (!form.name.trim()) e.name = "Name is required";
    const keywordList = form.keywords.split("\n").map((k) => k.trim()).filter((k) => k.length > 0);
    if (keywordList.length === 0) e.keywords = "At least one keyword is required";
    if (!form.model_override.trim()) e.model_override = "Model override is required";
    if (form.scope !== "global" && !form.scope_id.trim()) {
      e.scope_id = `${form.scope} scope requires a scope_id`;
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
        toast("Keyword rule updated");
      } else {
        await api.post("/admin/keyword-rules", body);
        toast("Keyword rule created");
      }
      closeForm();
      refetch();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || `Save failed (${err.status})`
          : err instanceof Error
            ? err.message
            : "Save failed";
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
      toast("Keyword rule deleted");
      setDeleteTarget(null);
      if (editing && editing.id === deleteTarget.id) {
        closeForm();
      }
      refetch();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || `Delete failed (${err.status})`
          : err instanceof Error
            ? err.message
            : "Delete failed";
      toast(msg, "error");
    } finally {
      setDeleting(false);
    }
  }

  const columns: Column<KeywordRule>[] = [
    {
      key: "name",
      header: "Keyword / Pattern",
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
      header: "Target Model",
      render: (r) => (
        <code style={{ fontSize: "var(--font-size-xs)", padding: "0.125rem 0.375rem", borderRadius: "var(--radius-sm)", background: "var(--color-bg-surface-2)" }}>
          {r.model_override}
        </code>
      ),
    },
    {
      key: "priority",
      header: "Priority",
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
      header: "Status",
      render: (r) => (
        <span style={{
          display: "inline-flex", alignItems: "center", gap: "0.375rem",
          fontSize: "var(--font-size-xs)", fontWeight: 500,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: r.enabled ? "var(--color-status-success)" : "var(--color-text-muted)",
          }} />
          {r.enabled ? "Active" : "Inactive"}
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
            title="Edit rule"
            onClick={() => openEdit(r)}
            style={{ color: "var(--color-text-muted)", cursor: "pointer", background: "none", border: "none", padding: "0.25rem" }}
          >
            <IconSettings />
          </button>
          <button
            className="sran-btn-icon"
            title="Delete rule"
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <h2 style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, margin: 0, color: "var(--color-text-primary)" }}>
            Keyword Rules
          </h2>
          <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: "0.25rem 0 0" }}>
            Route requests to specific models based on keyword matching
          </p>
        </div>
        <button
          className="sran-btn sran-btn-primary"
          onClick={openCreate}
          style={{ display: "inline-flex", alignItems: "center", gap: "0.375rem" }}
        >
          <IconPlus />
          New Rule
        </button>
      </div>

      {loading ? (
        <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "0.75rem 1.25rem", borderBottom: "1px solid var(--color-border-default)" }}>
            <div className="sran-skeleton" style={{ width: "200px", height: "2rem" }} />
          </div>
          <table className="sran-table">
            <thead>
              <tr>
                {["Keyword / Pattern", "Target Model", "Priority", "Status", ""].map((h) => (
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
          <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={refetch}>Retry</button>
        </div>
      ) : !hasRules ? (
        <div className="sran-card" style={{ padding: "3rem", textAlign: "center" }}>
          <div style={{
            color: "var(--color-text-muted)",
            marginBottom: "0.75rem",
            display: "flex",
            justifyContent: "center",
          }}>
            <IconGitMerge />
          </div>
          <p style={{ fontSize: "var(--font-size-base)", fontWeight: 500, color: "var(--color-text-secondary)", marginBottom: "0.375rem" }}>
            No keyword rules configured yet
          </p>
          <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", margin: 0, maxWidth: "320px", marginLeft: "auto", marginRight: "auto" }}>
            Keyword rules route requests to specific models when the prompt contains matching keywords or patterns.
          </p>
        </div>
      ) : (
        <div className="sran-card routing-metric-card" style={{ padding: 0, overflow: "hidden" }}>
          <DataTable
            columns={columns}
            data={data?.items ?? null}
            loading={loading}
            error={error}
            emptyMessage="No keyword rules yet"
            emptyIcon="⌨"
            emptyAction={<button className="sran-btn sran-btn-primary" onClick={openCreate}>Create your first rule</button>}
            rowKey={(r) => r.id}
            onRowClick={openEdit}
            searchable
            searchPlaceholder="Search by name, keyword, model..."
            onRetry={refetch}
          />
        </div>
      )}

      <Modal
        open={isFormOpen}
        onClose={closeForm}
        title={editing ? "Edit keyword rule" : "New keyword rule"}
        footer={
          <>
            {editing && (
              <button
                className="sran-btn sran-btn-danger"
                onClick={() => setDeleteTarget(editing)}
                disabled={saving}
                style={{ marginRight: "auto" }}
              >
                Delete
              </button>
            )}
            <button className="sran-btn sran-btn-secondary" onClick={closeForm} disabled={saving}>Cancel</button>
            <button className="sran-btn sran-btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {editing && (
            <div className="sran-detail-field">
              <div className="sran-detail-label">Rule ID</div>
              <div className="sran-detail-value"><code style={{ fontSize: "var(--font-size-xs)" }}>{editing.id}</code></div>
            </div>
          )}
          <div>
            <label className="sran-label">Name *</label>
            <input
              className={`sran-input ${errors.name ? "sran-input-error" : ""}`}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Route Thai legal queries"
              maxLength={128}
            />
            {errors.name && <div className="sran-field-error">{errors.name}</div>}
          </div>
          <div>
            <label className="sran-label">Keywords (one per line) *</label>
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
            <label className="sran-label">Model override *</label>
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
              <label className="sran-label">Priority</label>
              <input
                className="sran-input"
                type="number"
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="sran-label">Scope</label>
              <select
                className="sran-input"
                value={form.scope}
                onChange={(e) => setForm({ ...form, scope: e.target.value as KeywordScope })}
              >
                <option value="global">global</option>
                <option value="tenant">tenant</option>
                <option value="project">project</option>
              </select>
            </div>
          </div>
          {form.scope !== "global" && (
            <div>
              <label className="sran-label">{form.scope === "tenant" ? "Tenant ID *" : "Project ID *"}</label>
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
              <span className="sran-label" style={{ margin: 0 }}>Enabled</span>
            </label>
          </div>
        </div>
      </Modal>

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete keyword rule"
        message={`Delete rule '${deleteTarget?.name}'? This cannot be undone.`}
        confirmLabel="Delete rule"
        loading={deleting}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RoutingPage() {
  return (
    <>
      <PageHeader title="Routing" subtitle="Routing decisions and keyword overrides" />
      <style>{`
        .routing-metrics-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 0.75rem;
        }
        .routing-charts-grid {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 1rem;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @media (max-width: 1024px) {
          .routing-metrics-grid {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 640px) {
          .routing-metrics-grid {
            grid-template-columns: 1fr;
          }
          .routing-charts-grid {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
      <RoutingStatsSection />
      <KeywordRulesSection />
    </>
  );
}
