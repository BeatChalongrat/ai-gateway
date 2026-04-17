"use client";

import { useState } from "react";
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
// Routing stats — read-only sub-component
// ---------------------------------------------------------------------------

function RoutingStatsSection() {
  const { data, loading, error, refetch } = useApi<RoutingStats>("/admin/routing/stats");

  const modelColumns: Column<ModelStat>[] = [
    { key: "model", header: "Model", render: (r) => <code style={{ fontSize: "var(--font-size-xs)" }}>{r.model || "unknown"}</code> },
    { key: "count", header: "Count", render: (r) => r.count.toLocaleString() },
  ];

  const routerColumns: Column<RouterStat>[] = [
    {
      key: "router",
      header: "Router",
      render: (r) =>
        r.router ? (
          <span style={{ textTransform: "capitalize" }}>{r.router}</span>
        ) : (
          <Badge variant="neutral">—</Badge>
        ),
    },
    { key: "count", header: "Count", render: (r) => r.count.toLocaleString() },
  ];

  return (
    <div className="sran-card" style={{ padding: "1.25rem", marginBottom: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ fontSize: "var(--font-size-md)", fontWeight: 600, margin: 0 }}>Routing stats</h2>
        <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={refetch}>
          Refresh
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
        <div>
          <div className="sran-detail-label" style={{ marginBottom: "0.5rem" }}>Requests by model</div>
          <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
            <DataTable
              columns={modelColumns}
              data={data?.models ?? null}
              loading={loading}
              error={error}
              emptyMessage="No requests in the last 24 hours"
              emptyIcon="◇"
              rowKey={(r) => r.model || "unknown"}
              onRetry={refetch}
            />
          </div>
        </div>
        <div>
          <div className="sran-detail-label" style={{ marginBottom: "0.5rem" }}>Requests by router</div>
          <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
            <DataTable
              columns={routerColumns}
              data={data?.routers ?? null}
              loading={loading}
              error={error}
              emptyMessage="No router decisions in the last 24 hours"
              emptyIcon="◇"
              rowKey={(r) => r.router ?? "_null"}
              onRetry={refetch}
            />
          </div>
        </div>
      </div>
      <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.75rem", marginBottom: 0 }}>
        Stats cover the last 24 hours. Streaming requests show router = — until the streaming routing chain is wired.
      </p>
    </div>
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
      // If the deleted rule was the one being edited, also close that modal.
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
    { key: "name", header: "Name", render: (r) => <span style={{ fontWeight: 500 }}>{r.name}</span> },
    {
      key: "keywords",
      header: "Keywords",
      render: (r) => (
        <span style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-secondary)" }}>
          {truncate(r.keywords.join(", "), 40)}
        </span>
      ),
    },
    { key: "model_override", header: "Model override", render: (r) => <code style={{ fontSize: "var(--font-size-xs)" }}>{r.model_override}</code> },
    { key: "priority", header: "Priority", render: (r) => r.priority },
    { key: "scope", header: "Scope", render: (r) => <Badge variant={scopeBadgeVariant(r.scope)}>{r.scope}</Badge> },
    { key: "enabled", header: "Enabled", render: (r) => (r.enabled ? "✓" : "—") },
  ];

  const isFormOpen = creating || editing !== null;

  return (
    <div className="sran-card" style={{ padding: "1.25rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ fontSize: "var(--font-size-md)", fontWeight: 600, margin: 0 }}>Keyword rules</h2>
        <button className="sran-btn sran-btn-primary" onClick={openCreate}>+ New rule</button>
      </div>
      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RoutingPage() {
  return (
    <>
      <PageHeader title="Routing" subtitle="Routing decisions and keyword rules" />
      <RoutingStatsSection />
      <KeywordRulesSection />
    </>
  );
}
