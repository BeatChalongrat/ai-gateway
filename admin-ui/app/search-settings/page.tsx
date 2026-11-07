"use client";

import { useState, useMemo, useEffect } from "react";
import { outfit } from "../fonts";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";
import { useT, type DictKey } from "../../lib/i18n";

type Translate = (key: DictKey, vars?: Record<string, string | number>) => string;

const T = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  bl: "var(--border-default)", tp: "var(--text-primary)",
  ts: "var(--text-secondary)", tt: "var(--text-muted)",
  mono: "'JetBrains Mono','Fira Code',monospace",
};

const PROVIDERS = ["tavily", "brave", "serpapi", "google", "bing"] as const;
const PROVIDER_LABEL: Record<string, string> = {
  tavily: "Tavily", brave: "Brave Search", serpapi: "SerpAPI",
  google: "Google", bing: "Bing",
};
// Provider display names stay in English (brands); the sign-up hints are
// operator-facing copy, so they carry a dictionary key.
const PROVIDER_HINT: Record<string, DictKey> = {
  tavily: "infra.search.hint.tavily",
  brave: "infra.search.hint.brave",
  serpapi: "infra.search.hint.serpapi",
  google: "infra.search.hint.google",
  bing: "infra.search.hint.bing",
};

// health_status → dot color + label
function healthStyle(status: string, enabled: boolean, t: Translate): { color: string; label: string } {
  // Status palette via semantic tokens (theme-correct in dark + light).
  if (!enabled) return { color: "var(--text-muted)", label: t("field.disabled") };
  switch (status) {
    case "healthy": return { color: "var(--color-status-success)", label: t("infra.search.healthy") };
    case "degraded": return { color: "var(--color-status-warning)", label: t("infra.search.degraded") };
    case "down": return { color: "var(--color-status-error)", label: t("infra.search.down") };
    default: return { color: "var(--text-muted)", label: t("infra.search.notChecked") };
  }
}

function priorityBadge(i: number, t: Translate): string {
  return i === 0 ? t("infra.search.primary") : t("infra.search.fallback", { n: i });
}

function timeAgo(iso: string | null, t: Translate): string {
  if (!iso) return t("infra.never");
  const at = new Date(iso).getTime();
  const mins = Math.round((Date.now() - at) / 60000);
  if (mins < 1) return t("time.justNow");
  if (mins < 60) return t("time.minutesAgo", { n: mins });
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return t("time.hoursAgo", { n: hrs });
  return t("time.daysAgo", { n: Math.round(hrs / 24) });
}

interface Provider {
  id: string; provider: string; display_name: string | null;
  priority: number; is_enabled: boolean; max_results: number;
  api_key_masked: string; health_status: string;
  uptime_pct: number | null; avg_latency_ms: number | null;
  total_queries: number; total_failures: number;
  last_health_check: string | null; last_failure_at: string | null;
  last_failure_reason: string | null;
}
interface SearchConfig {
  search_enabled: boolean; search_credit_cost: number;
  providers: Provider[]; supported_providers: string[];
}

export default function SearchSettingsPage() {
  const { toast } = useToast();
  const t = useT();
  const { data: tenantsData } = useApi<{ items: any[] }>("/admin/tenants");
  const tenants = (tenantsData?.items || []).filter((x) => x.status !== "deleted");

  const [tenantId, setTenantId] = useState<string>("");
  useEffect(() => {
    if (!tenantId && tenants.length > 0) setTenantId(tenants[0].tenant_id);
  }, [tenants, tenantId]);

  const cfgPath = tenantId ? `/admin/tenants/${tenantId}/search-config` : "/admin/tenants";
  const { data: cfg, loading, refetch } = useApi<SearchConfig>(cfgPath);
  const { data: health, refetch: refetchHealth } = useApi<any>("/admin/search-health/summary");

  // Tenant config form (toggle + credit cost)
  const [enabled, setEnabled] = useState(false);
  const [creditCost, setCreditCost] = useState("5");
  const [savingCfg, setSavingCfg] = useState(false);
  useEffect(() => {
    if (cfg) {
      setEnabled(!!cfg.search_enabled);
      setCreditCost(String(cfg.search_credit_cost ?? 5));
    }
  }, [cfg]);

  const providers = useMemo(
    () => (cfg?.providers || []).slice().sort((a, b) => a.priority - b.priority),
    [cfg],
  );

  async function saveConfig() {
    if (!tenantId) return;
    setSavingCfg(true);
    try {
      await api.put(`/admin/tenants/${tenantId}/search-config`, {
        search_enabled: enabled,
        search_credit_cost: parseInt(creditCost, 10) || 0,
      });
      toast(t("infra.search.toastSaved"));
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastSaveFailed"), "error");
    } finally {
      setSavingCfg(false);
    }
  }

  // Add provider modal
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({ provider: "tavily", api_key: "", display_name: "", max_results: "5" });
  const [adding, setAdding] = useState(false);

  function openAdd() {
    setAddForm({ provider: "tavily", api_key: "", display_name: "", max_results: "5" });
    setShowAdd(true);
  }
  async function submitAdd() {
    if (!addForm.api_key.trim()) { toast(t("infra.search.errApiKeyRequired"), "error"); return; }
    setAdding(true);
    try {
      await api.post(`/admin/tenants/${tenantId}/search-providers`, {
        provider: addForm.provider,
        api_key: addForm.api_key.trim(),
        display_name: addForm.display_name.trim() || null,
        max_results: parseInt(addForm.max_results, 10) || 5,
      });
      toast(t("infra.search.toastAdded", { name: PROVIDER_LABEL[addForm.provider] }));
      setShowAdd(false);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastAddFailed"), "error");
    } finally {
      setAdding(false);
    }
  }

  // Edit provider modal
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [editForm, setEditForm] = useState({ api_key: "", display_name: "", max_results: "5" });
  const [saving, setSaving] = useState(false);
  function openEdit(p: Provider) {
    setEditForm({ api_key: "", display_name: p.display_name || "", max_results: String(p.max_results) });
    setEditTarget(p);
  }
  async function submitEdit() {
    if (!editTarget) return;
    setSaving(true);
    try {
      const body: any = {
        display_name: editForm.display_name.trim() || null,
        max_results: parseInt(editForm.max_results, 10) || 5,
      };
      if (editForm.api_key.trim()) body.api_key = editForm.api_key.trim();
      await api.put(`/admin/tenants/${tenantId}/search-providers/${editTarget.id}`, body);
      toast(t("infra.search.toastUpdated"));
      setEditTarget(null);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastUpdateFailed"), "error");
    } finally {
      setSaving(false);
    }
  }

  const [deleteTarget, setDeleteTarget] = useState<Provider | null>(null);
  const [deleting, setDeleting] = useState(false);
  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/tenants/${tenantId}/search-providers/${deleteTarget.id}`);
      toast(t("infra.search.toastRemoved"));
      setDeleteTarget(null);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastRemoveFailed"), "error");
    } finally {
      setDeleting(false);
    }
  }

  async function toggleProvider(p: Provider) {
    try {
      await api.put(`/admin/tenants/${tenantId}/search-providers/${p.id}`, { is_enabled: !p.is_enabled });
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastToggleFailed"), "error");
    }
  }

  async function move(p: Provider, dir: -1 | 1) {
    const idx = providers.findIndex((x) => x.id === p.id);
    const swap = idx + dir;
    if (swap < 0 || swap >= providers.length) return;
    const order = providers.map((x) => x.id);
    [order[idx], order[swap]] = [order[swap], order[idx]];
    try {
      await api.put(`/admin/tenants/${tenantId}/search-providers/reorder`, { provider_ids: order });
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastReorderFailed"), "error");
    }
  }

  const [testing, setTesting] = useState<string | null>(null);
  async function testProvider(p: Provider) {
    setTesting(p.id);
    try {
      const r = await api.post(`/admin/tenants/${tenantId}/search-providers/${p.id}/test`, {});
      if (r.status === "success") {
        toast(t("infra.search.toastTestOk", {
          name: PROVIDER_LABEL[p.provider],
          count: r.results_count,
          ms: r.latency_ms,
        }));
      } else {
        // Both halves are dynamic (brand name + upstream error text), so
        // there is nothing here to translate.
        toast(`${PROVIDER_LABEL[p.provider]}: ${r.error}`, "error");
      }
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.testFailed"), "error");
    } finally {
      setTesting(null);
    }
  }

  const [checkingHealth, setCheckingHealth] = useState(false);
  async function checkNow() {
    if (!tenantId) return;
    setCheckingHealth(true);
    try {
      await api.post(`/admin/tenants/${tenantId}/search-providers/check-health`, {});
      toast(t("infra.search.toastHealthOk"));
      refetch();
      refetchHealth();
    } catch (e: any) {
      toast(e.body?.error?.message || t("infra.search.toastHealthFailed"), "error");
    } finally {
      setCheckingHealth(false);
    }
  }

  const [menuOpen, setMenuOpen] = useState<string | null>(null);

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>{t("infra.search.title")}</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>{t("infra.search.subtitle")}</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <select className="sran-input" style={{ minWidth: 200 }} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
            {tenants.map((x) => <option key={x.tenant_id} value={x.tenant_id}>{x.name}</option>)}
          </select>
          <ThemeToggle />
        </div>
      </div>

      {/* Health Overview */}
      <div style={card()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
            <HealthCount color="#5DCAA5" label={t("infra.search.healthy")} n={health?.healthy ?? 0} />
            <HealthCount color="#EF9F27" label={t("infra.search.degraded")} n={health?.degraded ?? 0} />
            <HealthCount color="#E24B4A" label={t("infra.search.down")} n={health?.down ?? 0} />
            <HealthCount color="#888780" label={t("field.disabled")} n={health?.disabled ?? 0} />
            <span style={{ fontSize: 13, color: T.tt }}>{t("infra.search.lastChecked", { time: timeAgo(health?.last_check ?? null, t) })}</span>
          </div>
          <button className="sran-btn sran-btn-secondary" onClick={checkNow} disabled={checkingHealth || !tenantId}>
            {checkingHealth ? t("infra.search.checking") : t("infra.search.checkNow")}
          </button>
        </div>
      </div>

      {/* Tenant config */}
      <div style={card()}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.tp, marginBottom: 14 }}>{t("infra.search.tenantConfig")}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
            <Toggle on={enabled} onClick={() => setEnabled((v) => !v)} />
            <span style={{ fontSize: 14, fontWeight: 600, color: T.tp }}>{t("infra.search.enableSearch")}</span>
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, color: T.ts }}>{t("infra.search.creditCost")}</span>
            <input className="sran-input" type="number" min={0} max={10000} value={creditCost}
              onChange={(e) => setCreditCost(e.target.value)} style={{ width: 90 }} />
          </div>
          <button className="sran-btn sran-btn-primary" onClick={saveConfig} disabled={savingCfg}>
            {savingCfg ? t("action.saving") : t("action.save")}
          </button>
        </div>
      </div>

      {/* Providers */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "22px 0 12px" }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: T.tp }}>{t("infra.search.providersTitle")}</div>
        <button className="sran-btn sran-btn-primary" onClick={openAdd} disabled={!tenantId}>{t("infra.search.addProviderCta")}</button>
      </div>

      {loading ? (
        <div style={{ color: T.tt, fontSize: 14, padding: 16 }}>{t("state.loading")}</div>
      ) : providers.length === 0 ? (
        <div style={{ ...card(), color: T.tt, fontSize: 14 }}>{t("infra.search.noProviders")}</div>
      ) : (
        providers.map((p, i) => {
          const hs = healthStyle(p.health_status, p.is_enabled, t);
          return (
            <div key={p.id} style={card()}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: "#fff", background: i === 0 ? "var(--color-status-info)" : "#6b7280", borderRadius: 5, padding: "2px 7px" }}>{priorityBadge(i, t)}</span>
                    <span style={{ fontSize: 15, fontWeight: 700, color: T.tp }}>{PROVIDER_LABEL[p.provider] || p.provider}</span>
                    {p.display_name ? <span style={{ fontSize: 13, color: T.tt }}>· {p.display_name}</span> : null}
                  </div>
                  <div style={{ fontSize: 13, color: T.ts, fontFamily: T.mono }}>{t("infra.search.keyLine", { key: p.api_key_masked, n: p.max_results })}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
                    <span style={{ width: 9, height: 9, borderRadius: "50%", background: hs.color, display: "inline-block" }} />
                    <span style={{ fontSize: 13, fontWeight: 600, color: T.tp }} title={p.last_failure_reason || ""}>{hs.label}</span>
                    {p.is_enabled && p.health_status !== "unknown" ? (
                      <span style={{ fontSize: 13, color: T.tt }}>
                        {[
                          p.uptime_pct != null ? t("infra.search.uptime", { pct: p.uptime_pct }) : null,
                          p.avg_latency_ms != null ? t("infra.search.avgLatency", { ms: p.avg_latency_ms }) : null,
                          t("infra.search.queries", { n: p.total_queries }),
                        ].filter(Boolean).join(" · ")}
                      </span>
                    ) : null}
                    {(p.health_status === "down" || p.health_status === "degraded") && p.last_failure_reason ? (
                      <span style={{ fontSize: 12, color: "#E24B4A", marginLeft: 4, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.last_failure_reason}>
                        — {p.last_failure_reason}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, position: "relative" }}>
                  <Toggle on={p.is_enabled} onClick={() => toggleProvider(p)} />
                  <button className="sran-btn sran-btn-secondary" style={{ padding: "6px 9px" }} onClick={() => setMenuOpen(menuOpen === p.id ? null : p.id)}>⋮</button>
                  {menuOpen === p.id ? (
                    <>
                      <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setMenuOpen(null)} />
                      <div style={{ position: "absolute", right: 0, top: 38, zIndex: 50, background: T.card, border: `1px solid ${T.bl}`, borderRadius: 10, boxShadow: "0 8px 24px rgba(0,0,0,0.18)", overflow: "hidden", minWidth: 150 }}>
                        {[
                          { label: t("action.edit"), fn: () => { setMenuOpen(null); openEdit(p); } },
                          { label: testing === p.id ? t("action.testing") : t("action.test"), fn: () => { setMenuOpen(null); testProvider(p); } },
                          { label: t("infra.search.moveUp"), fn: () => { setMenuOpen(null); move(p, -1); }, dim: i === 0 },
                          { label: t("infra.search.moveDown"), fn: () => { setMenuOpen(null); move(p, 1); }, dim: i === providers.length - 1 },
                          { label: t("action.delete"), fn: () => { setMenuOpen(null); setDeleteTarget(p); }, danger: true },
                        ].map((it) => (
                          <button key={it.label} onClick={it.fn} disabled={(it as any).dim}
                            style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 14px", fontSize: 13, background: "none", border: "none", cursor: (it as any).dim ? "default" : "pointer", color: (it as any).danger ? "#E24B4A" : (it as any).dim ? T.tt : T.tp, opacity: (it as any).dim ? 0.5 : 1 }}>
                            {it.label}
                          </button>
                        ))}
                      </div>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })
      )}

      {/* Add modal */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title={t("infra.search.addModalTitle")} footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={() => setShowAdd(false)}>{t("action.cancel")}</button>
          <button className="sran-btn sran-btn-primary" onClick={submitAdd} disabled={adding}>{adding ? t("infra.search.adding") : t("infra.search.addProvider")}</button>
        </>
      }>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label className="sran-label">{t("field.provider")}</label>
            <select className="sran-input" value={addForm.provider} onChange={(e) => setAddForm({ ...addForm, provider: e.target.value })}>
              {PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABEL[p]}</option>)}
            </select>
            <div style={{ fontSize: 12, color: T.tt, marginTop: 6 }}>{PROVIDER_HINT[addForm.provider] ? t(PROVIDER_HINT[addForm.provider]) : ""}</div>
          </div>
          <div>
            <label className="sran-label">{t("infra.search.apiKey")}</label>
            <input className="sran-input" value={addForm.api_key} onChange={(e) => setAddForm({ ...addForm, api_key: e.target.value })} placeholder={addForm.provider === "google" ? "API_KEY:SEARCH_ENGINE_ID" : t("infra.search.apiKeyPlaceholder")} />
          </div>
          <div>
            <label className="sran-label">{t("infra.search.displayNameOptional")}</label>
            <input className="sran-input" value={addForm.display_name} onChange={(e) => setAddForm({ ...addForm, display_name: e.target.value })} placeholder={t("infra.search.displayNamePlaceholder")} />
          </div>
          <div>
            <label className="sran-label">{t("infra.search.maxResults")}</label>
            <input className="sran-input" type="number" min={1} max={10} value={addForm.max_results} onChange={(e) => setAddForm({ ...addForm, max_results: e.target.value })} style={{ width: 90 }} />
          </div>
        </div>
      </Modal>

      {/* Edit modal */}
      <Modal open={!!editTarget} onClose={() => setEditTarget(null)} title={editTarget ? t("infra.search.editTitle", { name: PROVIDER_LABEL[editTarget.provider] ?? editTarget.provider }) : t("action.edit")} footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={() => setEditTarget(null)}>{t("action.cancel")}</button>
          <button className="sran-btn sran-btn-primary" onClick={submitEdit} disabled={saving}>{saving ? t("action.saving") : t("action.save")}</button>
        </>
      }>
        {editTarget ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <label className="sran-label">{t("infra.search.apiKeyKeep")}</label>
              <input className="sran-input" value={editForm.api_key} onChange={(e) => setEditForm({ ...editForm, api_key: e.target.value })} placeholder={t("infra.search.currentKey", { key: editTarget.api_key_masked })} />
            </div>
            <div>
              <label className="sran-label">{t("infra.search.displayName")}</label>
              <input className="sran-input" value={editForm.display_name} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })} />
            </div>
            <div>
              <label className="sran-label">{t("infra.search.maxResults")}</label>
              <input className="sran-input" type="number" min={1} max={10} value={editForm.max_results} onChange={(e) => setEditForm({ ...editForm, max_results: e.target.value })} style={{ width: 90 }} />
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmModal
        open={!!deleteTarget}
        title={t("infra.search.removeTitle")}
        message={deleteTarget ? t("infra.search.removeMsg", { name: PROVIDER_LABEL[deleteTarget.provider] ?? deleteTarget.provider }) : ""}
        confirmLabel={t("action.remove")}
        loading={deleting}
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}

function card(): React.CSSProperties {
  return { background: T.card, border: `1px solid ${T.bl}`, borderRadius: 14, padding: 18, marginBottom: 12 };
}

function HealthCount({ color, label, n }: { color: string; label: string; n: number }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
      <span style={{ width: 10, height: 10, borderRadius: "50%", background: color }} />
      {n} {label}
    </span>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} type="button" aria-pressed={on}
      style={{ width: 40, height: 22, borderRadius: 999, border: "none", cursor: "pointer", position: "relative", background: on ? "#3b6cf6" : "var(--border-default)", transition: "background .15s" }}>
      <span style={{ position: "absolute", top: 2, left: on ? 20 : 2, width: 18, height: 18, borderRadius: "50%", background: "#fff", transition: "left .15s", boxShadow: "0 1px 2px rgba(0,0,0,0.3)" }} />
    </button>
  );
}
