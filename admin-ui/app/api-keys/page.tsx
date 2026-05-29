"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonCards } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

const T = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  bl: "var(--border-default)", bm: "var(--border-default)",
  tp: "var(--text-primary)", ts: "var(--text-secondary)",
  th: "var(--text-secondary)", tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  mono: "'JetBrains Mono','Fira Code',monospace",
};

const PALETTE = ["#3b6cf6", "#10b981", "#8b5cf6", "#f59e0b", "#06b6d4", "#ec4899", "#ef4444", "#6b7280"];
function colorFor(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

const CSS = `
@keyframes akFadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.ak-group{animation:akFadeUp 0.4s ease-out both}
.ak-row{transition:background 100ms}
.ak-row:hover{background:rgba(59,108,246,0.04)}
.ak-row .ak-acts{opacity:0;transition:opacity 0.15s ease}
.ak-row:hover .ak-acts{opacity:1}
`;

export default function ApiKeysPage() {
  const { data, loading, error, refetch } = useApi<{ items: any[] }>("/admin/api-keys");
  const projects = useApi<{ items: any[] }>("/admin/projects");
  const [showCreate, setShowCreate] = useState(false);
  const [rawKey, setRawKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<any | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<any | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [form, setForm] = useState({ tenant_id: "", project_id: "", name: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const { toast } = useToast();

  const items = data?.items ?? [];
  const projectItems = projects.data?.items ?? [];

  function projectName(pid: string) {
    const p = projectItems.find((p: any) => p.project_id === pid);
    return p ? p.name : pid.slice(0, 8) + "…";
  }

  // Stats
  const activeCount = items.filter((k) => k.status === "active").length;
  const revokedCount = items.filter((k) => k.status === "revoked").length;

  // Filter
  const filtered = useMemo(() => {
    let r = items;
    if (statusFilter) r = r.filter((k) => k.status === statusFilter);
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((k) => k.name?.toLowerCase().includes(q) || k.key_prefix?.toLowerCase().includes(q) || projectName(k.project_id).toLowerCase().includes(q));
    }
    return r;
  }, [items, statusFilter, search, projectItems]);

  // Group by project
  const grouped = useMemo(() => {
    const map = new Map<string, any[]>();
    filtered.forEach((k) => {
      const pid = k.project_id;
      if (!map.has(pid)) map.set(pid, []);
      map.get(pid)!.push(k);
    });
    return Array.from(map.entries()); // [project_id, keys[]]
  }, [filtered]);

  function toggleGroup(pid: string) {
    setCollapsed((c) => ({ ...c, [pid]: !c[pid] }));
  }

  // CRUD
  function validate() {
    const e: Record<string, string> = {};
    if (!form.project_id) e.project_id = "Project is required";
    if (!form.name.trim()) e.name = "Key name is required";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleCreate() {
    if (!validate()) return;
    setSaving(true);
    try {
      const result = await api.post("/admin/api-keys", form);
      setRawKey(result.key);
      setForm({ tenant_id: "", project_id: "", name: "" }); setErrors({});
      toast("API key created successfully"); refetch();
    } catch (e: any) { toast(e.body?.error?.message || "Failed to create key", "error"); }
    finally { setSaving(false); }
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await api.delete(`/admin/api-keys/${revokeTarget.key_id}`);
      toast("API key revoked"); setRevokeTarget(null); refetch();
    } catch (e: any) { toast(e.body?.error?.message || "Failed to revoke", "error"); }
    finally { setRevoking(false); }
  }

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>API Keys</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>Manage authentication keys for project access</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button className="sran-btn sran-btn-primary" onClick={() => setShowCreate(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Create Key
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* Search + filters */}
      {!loading && !error && items.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "0 1 300px", minWidth: 160 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search keys..."
              style={{ width: "100%", padding: "6px 24px 6px 30px", fontSize: 13, fontFamily: "inherit", border: `1.5px solid ${T.bl}`, borderRadius: 6, background: "var(--bg-base)", color: T.tp, outline: "none", transition: "border-color 120ms, box-shadow 120ms, background 120ms" }}
              onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.18)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
              onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
            />
            {search && <button onClick={() => setSearch("")} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: T.tt, fontSize: 12, padding: 2 }}>✕</button>}
          </div>

          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {[
              { key: null, label: "All", count: items.length, dot: null },
              { key: "active", label: "Active", count: activeCount, dot: "#10b981" },
              { key: "revoked", label: "Revoked", count: revokedCount, dot: "#ef4444" },
            ].map((f) => {
              const active = statusFilter === f.key;
              return (
                <button key={f.label} onClick={() => setStatusFilter(f.key)} style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  padding: "4px 10px", fontSize: 11, fontWeight: active ? 600 : 500, borderRadius: 6,
                  cursor: "pointer", fontFamily: "inherit",
                  border: active ? `1.5px solid ${T.accent}` : "1.5px solid transparent",
                  background: active ? "rgba(59,108,246,0.06)" : "transparent",
                  color: active ? T.accent : T.tt, transition: "all 120ms",
                }}>
                  {f.dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: f.dot, opacity: active ? 1 : 0.5 }} />}
                  {f.label} <span style={{ fontWeight: 700, fontSize: 10 }}>{f.count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <SkeletonCards count={3} />
      ) : error ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}><ErrorState message={error} onRetry={refetch} /></div>
      ) : items.length === 0 ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}>
          <EmptyState message="No API keys yet" icon="⚷" action={<button onClick={() => setShowCreate(true)} style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, fontFamily: "inherit", borderRadius: 8, border: "none", background: T.accent, color: "white", cursor: "pointer" }}>Create your first key</button>} />
        </div>
      ) : grouped.length === 0 ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: "40px 20px", textAlign: "center" }}>
          <p style={{ fontSize: 13, color: T.tt, margin: "0 0 12px" }}>No keys match the current filters</p>
          <button onClick={() => { setSearch(""); setStatusFilter(null); }} style={{ padding: "6px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 6, border: `1.5px solid ${T.bl}`, background: T.card, color: T.tp, cursor: "pointer" }}>Clear filters</button>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {grouped.map(([pid, keys], gi) => {
            const pName = projectName(pid);
            const pColor = colorFor(pName);
            const isCollapsed = collapsed[pid] ?? false;

            return (
              <div key={pid} className="ak-group" style={{ animationDelay: `${0.05 + gi * 0.05}s` }}>
                {/* Group header */}
                <button onClick={() => toggleGroup(pid)} style={{
                  width: "100%", display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 16px", background: "none", border: "none", cursor: "pointer",
                  fontFamily: "inherit", textAlign: "left",
                }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: 5, background: pColor, color: "white",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 12, fontWeight: 700, flexShrink: 0,
                  }}>{pName.charAt(0).toUpperCase()}</div>
                  <span style={{ fontSize: 15, fontWeight: 700, color: T.tp }}>{pName}</span>
                  <span style={{
                    fontFamily: T.mono, fontSize: 12, color: T.tt, background: "var(--bg-elevated)",
                    padding: "2px 8px", borderRadius: 100,
                  }}>{keys.length}</span>
                  <span style={{ flex: 1 }} />
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ transition: "transform 0.2s ease", transform: isCollapsed ? "rotate(0)" : "rotate(180deg)" }}>
                    <polyline points="6 9 12 15 18 9"/>
                  </svg>
                </button>

                {/* Group body */}
                {!isCollapsed && (
                  <div style={{
                    background: T.card, borderRadius: 10, border: `1px solid ${T.bl}`,
                    boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflowX: "auto",
                  }}>
                    {keys.map((k: any, ki: number) => {
                      const isActive = k.status === "active";
                      return (
                        <div key={k.key_id} className="ak-row" style={{
                          display: "flex", alignItems: "center", gap: 14,
                          padding: "14px 20px",
                          minWidth: 520,
                          borderBottom: ki < keys.length - 1 ? `1px solid ${T.bl}` : "none",
                          cursor: "pointer",
                        }} onClick={() => setSelected(k)}>
                          {/* Status dot */}
                          <span style={{
                            width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                            background: isActive ? "#10b981" : "#ef4444",
                          }} />
                          {/* Name */}
                          <span style={{ fontSize: "13.5px", fontWeight: 600, color: T.tp, width: 180, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k.name}</span>
                          {/* Key */}
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
                            <code style={{ fontFamily: T.mono, fontSize: 12, color: T.tt }}>{k.key_prefix}••••</code>
                            <button onClick={(e) => {
                              e.stopPropagation();
                              navigator.clipboard.writeText(k.key_prefix);
                              toast("Copied to clipboard");
                            }} style={{
                              background: "none", border: "none", cursor: "pointer", padding: 2,
                              color: T.tt, display: "flex", alignItems: "center",
                              transition: "color 100ms",
                            }}
                              onMouseEnter={(e) => { e.currentTarget.style.color = T.accent; }}
                              onMouseLeave={(e) => { e.currentTarget.style.color = T.tt; }}
                            >
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
                            </button>
                          </span>
                          {/* Status badge */}
                          <span style={{
                            display: "inline-block", padding: "2px 10px", borderRadius: 100,
                            fontSize: "10.5px", fontWeight: 600, width: 64, textAlign: "center",
                            color: isActive ? "#059669" : "#dc2626",
                            background: isActive ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)",
                          }}>{k.status}</span>
                          {/* Created */}
                          <span style={{ fontFamily: T.mono, fontSize: 12, color: T.tt, width: 90, flexShrink: 0 }}>
                            {k.created_at ? new Date(k.created_at).toLocaleDateString() : "—"}
                          </span>
                          {/* Actions */}
                          <div className="ak-acts" style={{ width: 72, flexShrink: 0, textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                            {isActive ? (
                              <button onClick={() => setRevokeTarget(k)} style={{
                                padding: "6px 12px", fontSize: 12, fontWeight: 500, fontFamily: "inherit",
                                borderRadius: 6, border: `1px solid rgba(217,119,6,0.3)`, background: "transparent",
                                color: "#d97706", cursor: "pointer", transition: "background 100ms",
                              }}
                                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(245,158,11,0.06)"; }}
                                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                              >Revoke</button>
                            ) : null}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Detail modal */}
      <Modal open={!!selected && !revokeTarget} onClose={() => setSelected(null)} title="API Key Detail" footer={<button className="sran-btn sran-btn-secondary" onClick={() => setSelected(null)}>Close</button>}>
        {selected && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
            <div className="sran-detail-field"><div className="sran-detail-label">Name</div><div className="sran-detail-value">{selected.name}</div></div>
            <div className="sran-detail-field"><div className="sran-detail-label">Status</div><div className="sran-detail-value">{selected.status}</div></div>
            <div className="sran-detail-field"><div className="sran-detail-label">Key Prefix</div><div className="sran-detail-value"><code>{selected.key_prefix}••••</code></div></div>
            <div className="sran-detail-field"><div className="sran-detail-label">Project</div><div className="sran-detail-value">{projectName(selected.project_id)}</div></div>
            <div className="sran-detail-field"><div className="sran-detail-label">Key ID</div><div className="sran-detail-value"><code style={{ fontSize: 11 }}>{selected.key_id}</code></div></div>
            <div className="sran-detail-field"><div className="sran-detail-label">Created</div><div className="sran-detail-value">{selected.created_at ? new Date(selected.created_at).toLocaleString() : "—"}</div></div>
            {selected.last_used_at && <div className="sran-detail-field"><div className="sran-detail-label">Last Used</div><div className="sran-detail-value">{new Date(selected.last_used_at).toLocaleString()}</div></div>}
          </div>
        )}
      </Modal>

      {/* Revoke confirm */}
      <ConfirmModal open={!!revokeTarget} onClose={() => setRevokeTarget(null)} onConfirm={handleRevoke} title="Revoke API Key" message={`Are you sure you want to revoke "${revokeTarget?.name}"? This action cannot be undone and will immediately invalidate the key.`} confirmLabel="Revoke Key" loading={revoking} />

      {/* Create modal */}
      <Modal open={showCreate && !rawKey} onClose={() => { setShowCreate(false); setErrors({}); }} title="Create API Key" footer={
        <><button className="sran-btn sran-btn-secondary" onClick={() => { setShowCreate(false); setErrors({}); }}>Cancel</button>
        <button className="sran-btn sran-btn-primary" onClick={handleCreate} disabled={saving}>{saving ? "Creating..." : "Create"}</button></>
      }>
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label className="sran-label">Project *</label>
            <select className={`sran-input ${errors.project_id ? "sran-input-error" : ""}`} value={form.project_id} onChange={(e) => {
              const proj = projectItems.find((p: any) => p.project_id === e.target.value);
              setForm({ ...form, project_id: e.target.value, tenant_id: proj?.tenant_id || "" });
            }}>
              <option value="">Select project...</option>
              {projectItems.map((p: any) => (<option key={p.project_id} value={p.project_id}>{p.name}</option>))}
            </select>
            {errors.project_id && <div className="sran-field-error">{errors.project_id}</div>}
          </div>
          <div>
            <label className="sran-label">Key Name *</label>
            <input className={`sran-input ${errors.name ? "sran-input-error" : ""}`} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Production CI Key" maxLength={64} />
            {errors.name && <div className="sran-field-error">{errors.name}</div>}
            <div className="sran-char-count">{form.name.length}/64</div>
          </div>
        </div>
      </Modal>

      {/* Raw key display */}
      <Modal open={!!rawKey} onClose={() => { setRawKey(null); setShowCreate(false); }} title="API Key Created" footer={
        <button className="sran-btn sran-btn-primary" onClick={() => { setRawKey(null); setShowCreate(false); }}>Done</button>
      }>
        <div style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)", borderRadius: 8, padding: "12px 14px", marginBottom: 14, fontSize: 13, color: "#92400e", lineHeight: 1.5 }}>
          Save this key now. It will not be shown again.
        </div>
        <div style={{ position: "relative" }}>
          <div style={{ fontFamily: T.mono, fontSize: 13, padding: "14px 16px", borderRadius: 10, background: "var(--bg-elevated)", border: `1px solid ${T.bl}`, userSelect: "all", wordBreak: "break-all", lineHeight: 1.6, color: T.tp }}>
            {rawKey}
          </div>
          <button onClick={() => { navigator.clipboard.writeText(rawKey || ""); toast("Key copied to clipboard"); }}
            style={{ position: "absolute", top: 8, right: 8, padding: "4px 10px", fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 4, border: `1px solid ${T.bl}`, background: T.card, color: T.ts, cursor: "pointer" }}>Copy</button>
        </div>
      </Modal>
    </div>
  );
}
