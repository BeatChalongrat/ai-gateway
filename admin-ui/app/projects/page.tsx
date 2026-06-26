"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonCards } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";
import ProjectDetailModal from "./_components/ProjectDetailModal";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

/* ── Tokens ───────────────────────────────────────────────────────── */

const T = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  bl: "var(--border-default)", bm: "var(--border-default)",
  tp: "var(--text-primary)", ts: "var(--text-secondary)",
  th: "var(--text-secondary)", tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  mono: "'JetBrains Mono','Fira Code',monospace",
};

const PALETTE = ["#8b5cf6", "#3b6cf6", "#06b6d4", "#f59e0b", "#10b981", "#ec4899", "#ef4444", "#6b7280"];
function colorFor(s: string, idx?: number) {
  if (idx !== undefined) return PALETTE[idx % PALETTE.length];
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/* ── CSS ──────────────────────────────────────────────────────────── */

const CSS = `
@keyframes pjFadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes pjDotPulse{0%,100%{opacity:1}50%{opacity:0.5}}
@keyframes pjDonutFill{from{stroke-dashoffset:var(--donut-circum)}to{stroke-dashoffset:var(--donut-offset)}}
.pj-anim{animation:pjFadeUp 0.4s ease-out both}
.pj-row{animation:pjFadeUp 0.35s ease-out both}
.pj-card{
  animation:pjFadeUp 0.4s ease-out both;
  transition:transform 0.25s ease,box-shadow 0.25s ease,border-color 0.25s ease;
}
.pj-card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,0.1);border-color:var(--border-strong)}
.pj-card .pj-card-acts{opacity:0;transform:translateY(4px);transition:opacity 0.2s ease,transform 0.2s ease}
.pj-card:hover .pj-card-acts{opacity:1;transform:translateY(0)}
.pj-table{width:100%;border-collapse:collapse}
.pj-table th{text-align:left;padding:0 20px;height:40px;font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;color:${T.th};border-bottom:1px solid ${T.bl};background:var(--bg-elevated);white-space:nowrap}
.pj-table td{padding:15px 20px;border-bottom:1px solid ${T.bl};vertical-align:middle;color:${T.tp}}
.pj-table tbody tr{transition:background 100ms}
.pj-table tbody tr:hover{background:color-mix(in srgb, var(--bg-elevated) 60%, transparent)}
.pj-table tbody tr:last-child td{border-bottom:none}
`;

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

export default function ProjectsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, loading, error, refetch } = useApi<{ items: any[] }>(
    `/admin/projects${showArchived ? "?include_archived=true" : ""}`
  );
  const tenants = useApi<{ items: any[] }>("/admin/tenants");
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<any | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [form, setForm] = useState({ tenant_id: "", name: "", policy_profile_id: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [policyFilter, setPolicyFilter] = useState<string | null>(null);
  const [view, setView] = useState<"grid" | "list">("grid");
  const { toast } = useToast();

  async function handleRestore(projectId: string) {
    try {
      await api.post(`/admin/projects/${projectId}/restore`, {});
      toast("Project restored");
      refetch();
    } catch {
      toast("Restore failed", "error");
    }
  }

  const items = data?.items ?? [];
  const tenantItems = tenants.data?.items ?? [];

  function tenantName(tid: string) {
    const t = tenantItems.find((t: any) => t.tenant_id === tid);
    return t ? t.name : tid.slice(0, 8) + "…";
  }

  function isProtected(p: any): boolean {
    const nm = (p.name || "").toLowerCase();
    if (nm === "demo" || nm === "demo project") return true;
    const t = tenantItems.find((t: any) => t.tenant_id === p.tenant_id);
    return t?.slug === "demo";
  }

  // Stats
  const activeCount = items.length; // all projects are active by default
  const policyDefault = items.filter((p) => !p.policy_profile_id || p.policy_profile_id === "default").length;
  const uniqueTenants = useMemo(() => {
    const map = new Map<string, string>();
    items.forEach((p) => { if (!map.has(p.tenant_id)) map.set(p.tenant_id, tenantName(p.tenant_id)); });
    return Array.from(map.entries()); // [tenant_id, name][]
  }, [items, tenantItems]);

  // Filter
  const filtered = useMemo(() => {
    let r = items;
    if (policyFilter === "default") r = r.filter((p) => !p.policy_profile_id || p.policy_profile_id === "default");
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((p) => p.name?.toLowerCase().includes(q) || tenantName(p.tenant_id).toLowerCase().includes(q));
    }
    return r;
  }, [items, policyFilter, search, tenantItems]);

  // CRUD
  function validate() {
    const e: Record<string, string> = {};
    if (!form.tenant_id) e.tenant_id = "Tenant is required";
    if (!form.name.trim()) e.name = "Name is required";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleCreate() {
    if (!validate()) return;
    setSaving(true);
    try {
      await api.post("/admin/projects", { ...form, policy_profile_id: form.policy_profile_id || null });
      setShowCreate(false); setForm({ tenant_id: "", name: "", policy_profile_id: "" }); setErrors({});
      toast("Project created successfully"); refetch();
    } catch (e: any) { toast(e.body?.error?.message || "Failed to create project", "error"); }
    finally { setSaving(false); }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/projects/${deleteTarget.project_id}`);
      toast(`Project "${deleteTarget.name}" deleted`); setDeleteTarget(null); refetch();
    } catch (e: any) { toast(e.body?.error?.message || "Failed to delete project", "error"); }
    finally { setDeleting(false); }
  }

  // Donut chart
  const donutR = 26; const donutC = 2 * Math.PI * donutR;
  const donutFill = items.length > 0 ? (activeCount / items.length) : 0;
  const donutOffset = donutC * (1 - donutFill);

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>Projects</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>Project-level configuration and policy assignment</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button className="sran-btn sran-btn-primary" onClick={() => setShowCreate(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Create Project
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* Info bar */}
      {!loading && !error && items.length > 0 && (
        <div className="pj-anim" style={{ animationDelay: "0.05s", background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", padding: "20px 28px", marginBottom: 16, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 28 }}>
          {/* Donut */}
          <div style={{ position: "relative", width: 64, height: 64, flexShrink: 0 }}>
            <svg width="64" height="64" viewBox="0 0 64 64" style={{ transform: "rotate(-90deg)" }}>
              <circle cx="32" cy="32" r={donutR} fill="none" stroke="var(--bg-elevated)" strokeWidth="6" />
              <circle cx="32" cy="32" r={donutR} fill="none" stroke="#10b981" strokeWidth="6" strokeLinecap="round"
                strokeDasharray={donutC} strokeDashoffset={donutOffset}
                style={{ transition: "stroke-dashoffset 0.8s ease" } as any}
              />
            </svg>
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: T.mono, fontSize: 16, fontWeight: 800, color: T.tp }}>
              {activeCount}/{items.length}
            </div>
          </div>

          {/* Stats */}
          <div style={{ display: "flex", gap: 32 }}>
            {[
              { v: items.length, l: "Total Projects", c: T.tp },
              { v: activeCount, l: "Active", c: "#059669" },
              { v: policyDefault, l: "Default Policy", c: T.tp },
            ].map((s) => (
              <div key={s.l}>
                <div style={{ fontFamily: T.mono, fontSize: 22, fontWeight: 700, color: s.c, lineHeight: 1, letterSpacing: "-0.02em" }}>{s.v}</div>
                <div style={{ fontSize: "11.5px", fontWeight: 500, color: T.tt, marginTop: 3 }}>{s.l}</div>
              </div>
            ))}
          </div>

          {/* Tenant avatars */}
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 0 }}>
            <div style={{ display: "flex", alignItems: "center" }}>
              {uniqueTenants.map(([tid, name], i) => (
                <div key={tid} title={name} style={{
                  width: 30, height: 30, borderRadius: "50%", border: "2px solid var(--bg-surface)",
                  background: colorFor("", i), color: "white", fontSize: 11, fontWeight: 700,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  marginLeft: i === 0 ? 0 : -8, zIndex: uniqueTenants.length - i,
                  cursor: "default", transition: "transform 0.15s ease, z-index 0s",
                  position: "relative",
                }}
                  onMouseEnter={(e) => { e.currentTarget.style.transform = "scale(1.15)"; e.currentTarget.style.zIndex = "20"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.transform = "scale(1)"; e.currentTarget.style.zIndex = String(uniqueTenants.length - i); }}
                >{name.charAt(0).toUpperCase()}</div>
              ))}
            </div>
            <span style={{ fontSize: 11, color: T.tt, fontWeight: 500, marginLeft: 10, whiteSpace: "nowrap" }}>{uniqueTenants.length} tenant{uniqueTenants.length !== 1 ? "s" : ""}</span>
          </div>
        </div>
      )}

      {/* Toolbar */}
      {!loading && !error && items.length > 0 && (
        <div className="pj-anim" style={{ animationDelay: "0.1s", display: "flex", alignItems: "center", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "0 1 300px", minWidth: 160 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search projects or tenants..."
              style={{ width: "100%", padding: "6px 24px 6px 30px", fontSize: 13, fontFamily: "inherit", border: `1.5px solid ${T.bl}`, borderRadius: 6, background: "var(--bg-base)", color: T.tp, outline: "none", transition: "border-color 120ms, box-shadow 120ms, background 120ms" }}
              onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.18)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
              onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
            />
            {search && <button onClick={() => setSearch("")} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: T.tt, fontSize: 12, padding: 2 }}><Icon name="x" size={12} /></button>}
          </div>

          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {[
              { key: null, label: "All", count: items.length },
              { key: "default", label: "Default Policy", count: policyDefault },
            ].map((f) => {
              const active = policyFilter === f.key;
              return (
                <button key={f.label} onClick={() => setPolicyFilter(f.key)} style={{
                  padding: "4px 10px", fontSize: 11, fontWeight: active ? 600 : 500, borderRadius: 6,
                  cursor: "pointer", fontFamily: "inherit",
                  border: active ? `1.5px solid ${T.accent}` : "1.5px solid transparent",
                  background: active ? "rgba(59,108,246,0.06)" : "transparent",
                  color: active ? T.accent : T.tt, transition: "all 120ms",
                }}>{f.label} <span style={{ fontWeight: 700, fontSize: 10 }}>{f.count}</span></button>
              );
            })}
          </div>

          <span style={{ flex: 1 }} />

          {/* Show archived toggle — soft-deleted projects are hidden
              by default; flipping this on requests include_archived=true
              from the backend so the list shows every row. */}
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              color: T.tt,
              cursor: "pointer",
              userSelect: "none",
              marginRight: 4,
            }}
          >
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              style={{ cursor: "pointer" }}
            />
            Show archived
          </label>

          {/* View toggle */}
          <div style={{ display: "flex", gap: 2, background: "var(--bg-elevated)", borderRadius: 6, padding: 2 }}>
            {(["grid", "list"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)} style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                width: 28, height: 24, borderRadius: 4, border: "none", cursor: "pointer",
                background: view === v ? "var(--bg-surface)" : "transparent",
                boxShadow: view === v ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
                color: view === v ? T.tp : T.tt, transition: "all 100ms",
              }}>
                {v === "grid" ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
                ) : (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <SkeletonCards count={6} />
      ) : error ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}><ErrorState message={error} onRetry={refetch} /></div>
      ) : items.length === 0 ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}>
          <EmptyState message="No projects yet" icon={<Icon name="folder" size={22} />} action={<button onClick={() => setShowCreate(true)} style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, fontFamily: "inherit", borderRadius: 8, border: "none", background: T.accent, color: "white", cursor: "pointer" }}>Create your first project</button>} />
        </div>
      ) : view === "grid" ? (
        /* ── Grid view ── */
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 18 }}>
          {filtered.map((p, i) => {
            const c = colorFor(p.name || "", i);
            const tName = tenantName(p.tenant_id);
            const policy = p.policy_profile_id || "default";
            const policyColor = policy === "strict" ? { color: "#d97706", bg: "rgba(245,158,11,0.08)" } : { color: T.accent, bg: "rgba(59,108,246,0.08)" };
            return (
              <div key={p.project_id} className="pj-card" style={{
                animationDelay: `${0.15 + i * 0.05}s`,
                background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`,
                borderTop: `4px solid ${c}`,
                boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
                overflow: "hidden", position: "relative", cursor: "pointer",
                opacity: p.archived_at ? 0.6 : 1,
              }} onClick={() => setSelected(p)}>
                <div style={{ padding: "20px 22px 18px" }}>
                  {/* Top row */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                    <div style={{
                      width: 42, height: 42, borderRadius: 8, background: c, color: "white",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 16, fontWeight: 800, boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
                    }}>{(p.name || "?").charAt(0).toUpperCase()}</div>
                    {p.archived_at ? (
                      <span style={{
                        display: "inline-flex", alignItems: "center", gap: 5,
                        padding: "3px 10px", borderRadius: 100,
                        fontSize: 11, fontWeight: 600, color: "var(--accent-warning)",
                        background: "rgba(245,158,11,0.12)",
                      }}>
                        archived
                      </span>
                    ) : (
                      <span style={{
                        display: "inline-flex", alignItems: "center", gap: 5,
                        padding: "3px 10px", borderRadius: 100,
                        fontSize: 11, fontWeight: 600, color: "#059669",
                        background: "rgba(16,185,129,0.08)",
                      }}>
                        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", animation: "pjDotPulse 2s ease-in-out infinite" }} />
                        active
                      </span>
                    )}
                  </div>

                  {/* Name + tenant */}
                  <div style={{ fontSize: 17, fontWeight: 700, color: T.tp, lineHeight: 1.3 }}>{p.name}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 4, color: T.tt, fontSize: 13 }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
                    {tName}
                  </div>

                  {/* Meta */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", borderTop: `1px solid ${T.bl}`, paddingTop: 14, marginTop: 18 }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt, marginBottom: 4 }}>Policy</div>
                      <span style={{ display: "inline-block", padding: "2px 10px", fontSize: 11, fontWeight: 600, color: policyColor.color, background: policyColor.bg, borderRadius: 100 }}>{policy}</span>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt, marginBottom: 4 }}>Created</div>
                      <span style={{ fontFamily: T.mono, fontSize: "12.5px", color: T.ts }}>{p.created_at ? new Date(p.created_at).toLocaleDateString() : "—"}</span>
                    </div>
                  </div>
                </div>

                {/* Hover actions — gradient uses the theme surface
                    colour so the bottom fade stays readable on both
                    light and dark themes (was hardcoded white before,
                    which painted dark-theme cards white and hid the
                    action button text). */}
                <div className="pj-card-acts" style={{
                  position: "absolute", bottom: 0, left: 0, right: 0,
                  background: "linear-gradient(transparent, var(--bg-surface) 40%)",
                  padding: "32px 22px 16px", display: "flex", gap: 8, justifyContent: "center",
                }} onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => setSelected(p)} style={{
                    display: "inline-flex", alignItems: "center", gap: 5, padding: "7px 14px",
                    fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 6,
                    border: "1px solid rgba(99,102,241,0.35)", background: "var(--bg-surface)", color: T.accent, cursor: "pointer",
                  }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                    Open
                  </button>
                  {p.archived_at ? (
                    <button onClick={() => handleRestore(p.project_id)} style={{
                      display: "inline-flex", alignItems: "center", gap: 5, padding: "7px 14px",
                      fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 6,
                      border: "1px solid rgba(16,185,129,0.35)", background: "var(--bg-surface)", color: "var(--accent-success)", cursor: "pointer",
                    }}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 1015.88-5.94L21 9"/><polyline points="21 3 21 9 15 9"/></svg>
                      Restore
                    </button>
                  ) : !isProtected(p) && (
                    <button onClick={() => setDeleteTarget(p)} style={{
                      display: "inline-flex", alignItems: "center", gap: 5, padding: "7px 14px",
                      fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 6,
                      border: "1px solid rgba(239,68,68,0.35)", background: "var(--bg-surface)", color: "var(--accent-danger)", cursor: "pointer",
                    }}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                      Delete
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* ── List view ── */
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflow: "hidden" }}>
          <div style={{ overflowX: "auto" }}>
            <table className="pj-table" style={{ minWidth: 600 }}>
              <thead><tr>
                <th style={{ paddingLeft: 20 }}>Project</th>
                <th style={{ width: 110 }}>Policy</th>
                <th style={{ textAlign: "center", width: 100 }}>Status</th>
                <th style={{ width: 110 }}>Created</th>
              </tr></thead>
              <tbody>
                {filtered.map((p, i) => {
                  const c = colorFor(p.name || "", i);
                  const tName = tenantName(p.tenant_id);
                  const policy = p.policy_profile_id || "default";
                  const policyColor = policy === "strict" ? { color: "#d97706", bg: "rgba(245,158,11,0.08)" } : { color: T.accent, bg: "rgba(59,108,246,0.08)" };
                  return (
                    <tr key={p.project_id} className="pj-row" style={{ animationDelay: `${0.2 + i * 0.03}s`, cursor: "pointer", opacity: p.archived_at ? 0.6 : 1 }} onClick={() => setSelected(p)}>
                      <td style={{ paddingLeft: 20 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                          <div style={{ width: 34, height: 34, borderRadius: 6, background: c, color: "white", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, fontWeight: 700, flexShrink: 0 }}>{(p.name || "?").charAt(0).toUpperCase()}</div>
                          <div>
                            <div style={{ fontSize: 14, fontWeight: 600, color: T.tp }}>{p.name}</div>
                            <div style={{ fontSize: 12, color: T.tt, marginTop: 1 }}>{tName}</div>
                          </div>
                        </div>
                      </td>
                      <td><span style={{ display: "inline-block", padding: "2px 10px", fontSize: 11, fontWeight: 600, color: policyColor.color, background: policyColor.bg, borderRadius: 100 }}>{policy}</span></td>
                      <td style={{ textAlign: "center" }}>
                        {p.archived_at ? (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 10px", borderRadius: 100, fontSize: "11.5px", fontWeight: 600, color: "var(--accent-warning)", background: "rgba(245,158,11,0.12)" }}>
                            archived
                          </span>
                        ) : (
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 10px", borderRadius: 100, fontSize: "11.5px", fontWeight: 600, color: "#059669", background: "rgba(16,185,129,0.08)" }}>
                            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", animation: "pjDotPulse 2s ease-in-out infinite" }} />
                            active
                          </span>
                        )}
                      </td>
                      <td><span style={{ fontFamily: T.mono, fontSize: "12.5px", color: T.ts }}>{p.created_at ? new Date(p.created_at).toLocaleDateString() : "—"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      <ConfirmModal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} title="Delete Project" message={`Delete project "${deleteTarget?.name}"? Associated API keys will be orphaned.`} confirmLabel="Delete Project" loading={deleting} />

      {/* Detail modal — new in 2026-04-24 redesign: sections for
          Overview, Activity, Members, Danger zone. Backed by the
          expanded GET /admin/projects/{id} endpoint. */}
      <ProjectDetailModal
        open={!!selected && !deleteTarget}
        projectId={selected?.project_id ?? null}
        onClose={() => setSelected(null)}
        onMutated={refetch}
      />

      {/* Create modal */}
      <Modal open={showCreate} onClose={() => { setShowCreate(false); setErrors({}); }} title="Create Project" footer={
        <><button className="sran-btn sran-btn-secondary" onClick={() => { setShowCreate(false); setErrors({}); }}>Cancel</button>
        <button className="sran-btn sran-btn-primary" onClick={handleCreate} disabled={saving}>{saving ? "Creating..." : "Create"}</button></>
      }>
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label className="sran-label">Tenant *</label>
            <select className={`sran-input ${errors.tenant_id ? "sran-input-error" : ""}`} value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}>
              <option value="">Select tenant...</option>
              {tenantItems.filter((t: any) => t.status === "active").map((t: any) => (<option key={t.tenant_id} value={t.tenant_id}>{t.name} ({t.slug})</option>))}
            </select>
            {errors.tenant_id && <div className="sran-field-error">{errors.tenant_id}</div>}
          </div>
          <div>
            <label className="sran-label">Name *</label>
            <input className={`sran-input ${errors.name ? "sran-input-error" : ""}`} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Production" maxLength={64} />
            {errors.name && <div className="sran-field-error">{errors.name}</div>}
            <div className="sran-char-count">{form.name.length}/64</div>
          </div>
          <div>
            <label className="sran-label">Policy Profile</label>
            <select className="sran-input" value={form.policy_profile_id} onChange={(e) => setForm({ ...form, policy_profile_id: e.target.value })}>
              <option value="">Default</option><option value="default">default</option><option value="strict">strict</option>
            </select>
          </div>
        </div>
      </Modal>
    </div>
  );
}
