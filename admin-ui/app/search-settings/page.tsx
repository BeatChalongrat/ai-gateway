"use client";

import { useState, useMemo, useEffect } from "react";
import { Outfit } from "next/font/google";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

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
const PROVIDER_HINT: Record<string, string> = {
  tavily: "Get your API key at tavily.com. Free tier: 1,000 queries/month.",
  brave: "Get your API key at brave.com/search/api. Free tier: 2,000 queries/month.",
  serpapi: "Get your API key at serpapi.com. Free tier: 100 queries/month.",
  google: "Enter as API_KEY:SEARCH_ENGINE_ID. Requires a Custom Search Engine.",
  bing: "Get your key from the Azure Cognitive Services portal.",
};

// health_status → dot color + label
function healthStyle(status: string, enabled: boolean): { color: string; label: string } {
  if (!enabled) return { color: "#888780", label: "Disabled" };
  switch (status) {
    case "healthy": return { color: "#5DCAA5", label: "Healthy" };
    case "degraded": return { color: "#EF9F27", label: "Degraded" };
    case "down": return { color: "#E24B4A", label: "Down" };
    default: return { color: "#888780", label: "Not checked" };
  }
}

function priorityBadge(i: number): string {
  return i === 0 ? "Primary" : `Fallback ${i}`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
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
  const { data: tenantsData } = useApi<{ items: any[] }>("/admin/tenants");
  const tenants = (tenantsData?.items || []).filter((t) => t.status !== "deleted");

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
      toast("Search settings saved");
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || "Failed to save settings", "error");
    } finally {
      setSavingCfg(false);
    }
  }

  // Add provider modal
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({ provider: "tavily", api_key: "", display_name: "", max_results: "5" });
  const [adding, setAdding] = useState(false);
  const [addTestResult, setAddTestResult] = useState<string | null>(null);

  function openAdd() {
    setAddForm({ provider: "tavily", api_key: "", display_name: "", max_results: "5" });
    setAddTestResult(null);
    setShowAdd(true);
  }
  async function submitAdd() {
    if (!addForm.api_key.trim()) { toast("API key is required", "error"); return; }
    setAdding(true);
    try {
      await api.post(`/admin/tenants/${tenantId}/search-providers`, {
        provider: addForm.provider,
        api_key: addForm.api_key.trim(),
        display_name: addForm.display_name.trim() || null,
        max_results: parseInt(addForm.max_results, 10) || 5,
      });
      toast(`${PROVIDER_LABEL[addForm.provider]} added`);
      setShowAdd(false);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || "Failed to add provider", "error");
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
      toast("Provider updated");
      setEditTarget(null);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || "Failed to update", "error");
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
      toast("Provider removed");
      setDeleteTarget(null);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || "Failed to remove", "error");
    } finally {
      setDeleting(false);
    }
  }

  async function toggleProvider(p: Provider) {
    try {
      await api.put(`/admin/tenants/${tenantId}/search-providers/${p.id}`, { is_enabled: !p.is_enabled });
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || "Failed to toggle", "error");
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
      toast(e.body?.error?.message || "Failed to reorder", "error");
    }
  }

  const [testing, setTesting] = useState<string | null>(null);
  async function testProvider(p: Provider) {
    setTesting(p.id);
    try {
      const r = await api.post(`/admin/tenants/${tenantId}/search-providers/${p.id}/test`, {});
      if (r.status === "success") {
        toast(`✓ ${PROVIDER_LABEL[p.provider]}: ${r.results_count} results in ${r.latency_ms}ms`);
      } else {
        toast(`✗ ${PROVIDER_LABEL[p.provider]}: ${r.error}`, "error");
      }
    } catch (e: any) {
      toast(e.body?.error?.message || "Test failed", "error");
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
      toast("Health check complete");
      refetch();
      refetchHealth();
    } catch (e: any) {
      toast(e.body?.error?.message || "Health check failed", "error");
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
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>Search Settings</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>Web search providers, fallback chain, and health</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <select className="sran-input" style={{ minWidth: 200 }} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
            {tenants.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>)}
          </select>
          <ThemeToggle />
        </div>
      </div>

      {/* Health Overview */}
      <div style={card()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
            <HealthCount color="#5DCAA5" label="Healthy" n={health?.healthy ?? 0} />
            <HealthCount color="#EF9F27" label="Degraded" n={health?.degraded ?? 0} />
            <HealthCount color="#E24B4A" label="Down" n={health?.down ?? 0} />
            <HealthCount color="#888780" label="Disabled" n={health?.disabled ?? 0} />
            <span style={{ fontSize: 13, color: T.tt }}>Last checked: {timeAgo(health?.last_check ?? null)}</span>
          </div>
          <button className="sran-btn sran-btn-secondary" onClick={checkNow} disabled={checkingHealth || !tenantId}>
            {checkingHealth ? "Checking…" : "Check Now"}
          </button>
        </div>
      </div>

      {/* Tenant config */}
      <div style={card()}>
        <div style={{ fontSize: 16, fontWeight: 700, color: T.tp, marginBottom: 14 }}>Tenant Config</div>
        <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
            <Toggle on={enabled} onClick={() => setEnabled((v) => !v)} />
            <span style={{ fontSize: 14, fontWeight: 600, color: T.tp }}>Enable web search</span>
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, color: T.ts }}>Credit cost / query</span>
            <input className="sran-input" type="number" min={0} max={10000} value={creditCost}
              onChange={(e) => setCreditCost(e.target.value)} style={{ width: 90 }} />
          </div>
          <button className="sran-btn sran-btn-primary" onClick={saveConfig} disabled={savingCfg}>
            {savingCfg ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {/* Providers */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "22px 0 12px" }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: T.tp }}>Search Providers</div>
        <button className="sran-btn sran-btn-primary" onClick={openAdd} disabled={!tenantId}>+ Add Provider</button>
      </div>

      {loading ? (
        <div style={{ color: T.tt, fontSize: 14, padding: 16 }}>Loading…</div>
      ) : providers.length === 0 ? (
        <div style={{ ...card(), color: T.tt, fontSize: 14 }}>No providers configured. Add one to enable web search.</div>
      ) : (
        providers.map((p, i) => {
          const hs = healthStyle(p.health_status, p.is_enabled);
          return (
            <div key={p.id} style={card()}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: "#fff", background: i === 0 ? "#3b6cf6" : "#6b7280", borderRadius: 5, padding: "2px 7px" }}>{priorityBadge(i)}</span>
                    <span style={{ fontSize: 15, fontWeight: 700, color: T.tp }}>{PROVIDER_LABEL[p.provider] || p.provider}</span>
                    {p.display_name ? <span style={{ fontSize: 13, color: T.tt }}>· {p.display_name}</span> : null}
                  </div>
                  <div style={{ fontSize: 13, color: T.ts, fontFamily: T.mono }}>Key: {p.api_key_masked} · Max results: {p.max_results}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8 }}>
                    <span style={{ width: 9, height: 9, borderRadius: "50%", background: hs.color, display: "inline-block" }} />
                    <span style={{ fontSize: 13, fontWeight: 600, color: T.tp }} title={p.last_failure_reason || ""}>{hs.label}</span>
                    {p.is_enabled && p.health_status !== "unknown" ? (
                      <span style={{ fontSize: 13, color: T.tt }}>
                        {p.uptime_pct != null ? `${p.uptime_pct}% uptime` : ""}
                        {p.avg_latency_ms != null ? ` · ${p.avg_latency_ms}ms avg` : ""}
                        {` · ${p.total_queries} queries`}
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
                          { label: "Edit", fn: () => { setMenuOpen(null); openEdit(p); } },
                          { label: testing === p.id ? "Testing…" : "Test", fn: () => { setMenuOpen(null); testProvider(p); } },
                          { label: "Move up", fn: () => { setMenuOpen(null); move(p, -1); }, dim: i === 0 },
                          { label: "Move down", fn: () => { setMenuOpen(null); move(p, 1); }, dim: i === providers.length - 1 },
                          { label: "Delete", fn: () => { setMenuOpen(null); setDeleteTarget(p); }, danger: true },
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
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add Search Provider" footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={() => setShowAdd(false)}>Cancel</button>
          <button className="sran-btn sran-btn-primary" onClick={submitAdd} disabled={adding}>{adding ? "Adding…" : "Add Provider"}</button>
        </>
      }>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label className="sran-label">Provider</label>
            <select className="sran-input" value={addForm.provider} onChange={(e) => { setAddForm({ ...addForm, provider: e.target.value }); setAddTestResult(null); }}>
              {PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_LABEL[p]}</option>)}
            </select>
            <div style={{ fontSize: 12, color: T.tt, marginTop: 6 }}>{PROVIDER_HINT[addForm.provider]}</div>
          </div>
          <div>
            <label className="sran-label">API key</label>
            <input className="sran-input" value={addForm.api_key} onChange={(e) => setAddForm({ ...addForm, api_key: e.target.value })} placeholder={addForm.provider === "google" ? "API_KEY:SEARCH_ENGINE_ID" : "Paste API key"} />
          </div>
          <div>
            <label className="sran-label">Display name (optional)</label>
            <input className="sran-input" value={addForm.display_name} onChange={(e) => setAddForm({ ...addForm, display_name: e.target.value })} placeholder="e.g. Production Tavily" />
          </div>
          <div>
            <label className="sran-label">Max results (1–10)</label>
            <input className="sran-input" type="number" min={1} max={10} value={addForm.max_results} onChange={(e) => setAddForm({ ...addForm, max_results: e.target.value })} style={{ width: 90 }} />
          </div>
          {addTestResult ? <div style={{ fontSize: 13, color: T.ts }}>{addTestResult}</div> : null}
        </div>
      </Modal>

      {/* Edit modal */}
      <Modal open={!!editTarget} onClose={() => setEditTarget(null)} title={editTarget ? `Edit ${PROVIDER_LABEL[editTarget.provider]}` : "Edit"} footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={() => setEditTarget(null)}>Cancel</button>
          <button className="sran-btn sran-btn-primary" onClick={submitEdit} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </>
      }>
        {editTarget ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <label className="sran-label">API key (leave blank to keep current)</label>
              <input className="sran-input" value={editForm.api_key} onChange={(e) => setEditForm({ ...editForm, api_key: e.target.value })} placeholder={`Current: ${editTarget.api_key_masked}`} />
            </div>
            <div>
              <label className="sran-label">Display name</label>
              <input className="sran-input" value={editForm.display_name} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })} />
            </div>
            <div>
              <label className="sran-label">Max results (1–10)</label>
              <input className="sran-input" type="number" min={1} max={10} value={editForm.max_results} onChange={(e) => setEditForm({ ...editForm, max_results: e.target.value })} style={{ width: 90 }} />
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmModal
        open={!!deleteTarget}
        title="Remove provider"
        message={deleteTarget ? `Remove ${PROVIDER_LABEL[deleteTarget.provider]}? The remaining providers will re-prioritize.` : ""}
        confirmLabel="Remove"
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
