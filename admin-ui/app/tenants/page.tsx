"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";
import { useT, type DictKey } from "../../lib/i18n";

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

const AVATAR_COLORS = ["#3b6cf6", "#8b5cf6", "#ef4444", "#10b981", "#f59e0b", "#6b7280", "#06b6d4", "#ec4899"];
function avatarColor(name: string) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

/* ── Region pill colors ───────────────────────────────────────────── */

function regionStyle(r: string): { color: string; bg: string } {
  if (r === "global") return { color: "#3b6cf6", bg: "rgba(59,108,246,0.08)" };
  if (r === "us") return { color: "#8b5cf6", bg: "rgba(139,92,246,0.08)" };
  if (r.startsWith("ap")) return { color: "#06b6d4", bg: "rgba(6,182,212,0.08)" };
  if (r.startsWith("eu")) return { color: "#059669", bg: "rgba(16,185,129,0.08)" };
  return { color: T.ts, bg: "rgba(107,114,128,0.08)" };
}

/* ── Status config ────────────────────────────────────────────────── */

const STATUS: Record<string, { color: string; bg: string; dot: string; glow: boolean }> = {
  active:    { color: "#059669", bg: "rgba(16,185,129,0.08)", dot: "#10b981", glow: true },
  deleted:   { color: "#dc2626", bg: "rgba(239,68,68,0.08)",  dot: "#ef4444", glow: false },
  suspended: { color: "#d97706", bg: "rgba(245,158,11,0.08)", dot: "#f59e0b", glow: false },
};

/* ── CSS ──────────────────────────────────────────────────────────── */

const CSS = `
@keyframes tnFadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes tnPulse{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}
@keyframes tnSwing{0%,100%{transform:rotate(0)}25%{transform:rotate(6deg)}75%{transform:rotate(-6deg)}}
@keyframes tnGlow{0%,100%{opacity:1}50%{opacity:0.6}}
@keyframes dotGlow{0%,100%{opacity:1}50%{opacity:0.5}}
.tn-stat{animation:tnFadeUp 0.4s ease-out both}
.tn-row{animation:tnFadeUp 0.35s ease-out both}
.tn-card{animation:tnFadeUp 0.4s ease-out both}
.tn-icon-pulse svg{animation:tnPulse 2.5s ease-in-out infinite}
.tn-icon-glow svg{animation:tnGlow 2s ease-in-out infinite}
.tn-icon-swing svg{animation:tnSwing 3s ease-in-out infinite}
.tn-table{width:100%;border-collapse:collapse}
.tn-table th{text-align:left;padding:0 20px;height:40px;font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;color:${T.th};border-bottom:1px solid ${T.bl};background:var(--bg-elevated);white-space:nowrap}
.tn-table td{padding:16px 20px;border-bottom:1px solid ${T.bl};vertical-align:middle;color:${T.tp}}
.tn-table tbody tr{transition:background 100ms}
.tn-table tbody tr:hover{background:color-mix(in srgb, var(--bg-elevated) 60%, transparent)}
.tn-table tbody tr:last-child td{border-bottom:none}
.tn-table tbody tr .tn-acts{opacity:0;transition:opacity 0.15s}
.tn-table tbody tr:hover .tn-acts{opacity:1}
`;

/* ── Stat card ────────────────────────────────────────────────────── */

function StatCard({ value, label, icon, accent, delay = 0, iconClass }: {
  value: number; label: string; icon: React.ReactNode; accent: string; delay?: number; iconClass?: string;
}) {
  return (
    <div className="tn-stat" style={{
      animationDelay: `${delay}ms`,
      background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`,
      boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
      padding: "14px 16px", display: "flex", alignItems: "center", gap: 12, flex: 1, minWidth: 0,
    }}>
      <div className={iconClass} style={{
        width: 42, height: 42, borderRadius: 10, flexShrink: 0,
        background: `${accent}14`, display: "flex", alignItems: "center", justifyContent: "center", color: accent,
      }}>{icon}</div>
      <div>
        <div style={{ fontFamily: T.mono, fontSize: 24, fontWeight: 700, color: T.tp, lineHeight: 1, letterSpacing: "-0.02em" }}>{value}</div>
        <div style={{ fontSize: "12.5px", fontWeight: 500, color: T.ts, marginTop: 3 }}>{label}</div>
      </div>
    </div>
  );
}

/* ── Pill ──────────────────────────────────────────────────────────── */

function Pill({ text, color, bg, size = "11px" }: { text: string; color: string; bg: string; size?: string }) {
  return <span style={{ display: "inline-block", padding: "2px 10px", fontSize: size, fontWeight: 600, color, background: bg, borderRadius: 100, textTransform: "capitalize", lineHeight: 1.6 }}>{text}</span>;
}

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

const iconHouse = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>;
const iconCheck = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>;
const iconX = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>;
const iconGlobe = <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>;

export default function TenantsPage() {
  const t = useT();
  const { data, loading, error, refetch } = useApi<{ items: any[] }>("/admin/tenants");
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<any | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<any | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", region: "global" });
  // Field errors are held as dictionary KEYS, not rendered text, so an
  // error already on screen re-labels itself when the language is switched.
  const [errors, setErrors] = useState<Record<string, DictKey>>({});
  const [saving, setSaving] = useState(false);
  // Edit-flow state. Separate from the create form so an open Edit
  // modal doesn't get clobbered by a "Create" click and vice versa.
  // Tenant slug is intentionally NOT editable — it's the URL-stable
  // identifier and changing it would break every cached resource.
  const [editTarget, setEditTarget] = useState<any | null>(null);
  const [editForm, setEditForm] = useState({ name: "", region: "global", status: "active", credit_markup: "1.0" });
  const [editErrors, setEditErrors] = useState<Record<string, DictKey>>({});
  const [updating, setUpdating] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const { toast } = useToast();

  const items = data?.items ?? [];

  const stats = useMemo(() => {
    const active = items.filter((row) => row.status === "active").length;
    const deleted = items.filter((row) => row.status === "deleted").length;
    const regions = new Set(items.map((row) => row.region)).size;
    return { total: items.length, active, deleted, regions };
  }, [items]);

  const filtered = useMemo(() => {
    let r = items;
    if (statusFilter) r = r.filter((row) => row.status === statusFilter);
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((row) => row.name?.toLowerCase().includes(q) || row.slug?.toLowerCase().includes(q) || row.region?.toLowerCase().includes(q));
    }
    return r;
  }, [items, statusFilter, search]);

  function validate() {
    const e: Record<string, DictKey> = {};
    if (!form.name.trim()) e.name = "tenancy.err.nameRequired";
    if (!form.slug.trim()) e.slug = "tenancy.err.slugRequired";
    else if (!/^[a-z0-9-]+$/.test(form.slug)) e.slug = "tenancy.err.slugFormat";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleCreate() {
    if (!validate()) return;
    setSaving(true);
    try {
      await api.post("/admin/tenants", form);
      setShowCreate(false); setForm({ name: "", slug: "", region: "global" }); setErrors({});
      toast(t("tenancy.tenants.created")); refetch();
    } catch (e: any) { toast(e.body?.error?.message || t("tenancy.tenants.createFailed"), "error"); }
    finally { setSaving(false); }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/tenants/${deleteTarget.tenant_id}`);
      toast(t("tenancy.tenants.deletedToast", { name: deleteTarget.name })); setDeleteTarget(null); refetch();
    } catch (e: any) { toast(e.body?.error?.message || t("tenancy.tenants.deleteFailed"), "error"); }
    finally { setDeleting(false); }
  }

  async function handleRestore() {
    if (!restoreTarget) return;
    setRestoring(true);
    try {
      await api.put(`/admin/tenants/${restoreTarget.tenant_id}`, { status: "active" });
      toast(t("tenancy.tenants.restoredToast", { name: restoreTarget.name })); setRestoreTarget(null); refetch();
    } catch (e: any) { toast(e.body?.error?.message || t("tenancy.tenants.restoreFailed"), "error"); }
    finally { setRestoring(false); }
  }

  function openEdit(tenant: any) {
    setEditForm({
      name: tenant.name || "",
      region: tenant.region || "global",
      status: tenant.status || "active",
      credit_markup: String(tenant.credit_markup ?? 1.0),
    });
    setEditErrors({});
    setEditTarget(tenant);
  }

  function validateEdit() {
    const e: Record<string, DictKey> = {};
    if (!editForm.name.trim()) e.name = "tenancy.err.nameRequired";
    const markup = parseFloat(editForm.credit_markup);
    if (Number.isNaN(markup) || markup < 1.0 || markup > 5.0) {
      e.credit_markup = "tenancy.err.markupRange";
    }
    setEditErrors(e);
    return Object.keys(e).length === 0;
  }

  async function handleUpdate() {
    if (!editTarget || !validateEdit()) return;
    setUpdating(true);
    try {
      // Only send fields that actually changed so the audit log
      // records the real diff rather than a blanket overwrite.
      const body: Record<string, any> = {};
      if (editForm.name !== editTarget.name) body.name = editForm.name.trim();
      if (editForm.region !== editTarget.region) body.region = editForm.region;
      if (editForm.status !== editTarget.status) body.status = editForm.status;
      const newMarkup = parseFloat(editForm.credit_markup);
      if (newMarkup !== Number(editTarget.credit_markup ?? 1.0)) body.credit_markup = newMarkup;
      if (Object.keys(body).length === 0) {
        toast(t("tenancy.tenants.noChanges"));
        setEditTarget(null);
        return;
      }
      await api.put(`/admin/tenants/${editTarget.tenant_id}`, body);
      toast(t("tenancy.tenants.updatedToast", { name: editForm.name }));
      setEditTarget(null);
      refetch();
    } catch (e: any) {
      toast(e.body?.error?.message || t("tenancy.tenants.updateFailed"), "error");
    } finally {
      setUpdating(false);
    }
  }

  const activeCount = stats.active;
  const deletedCount = stats.deleted;

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>{t("tenancy.tenants.title")}</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>{t("tenancy.tenants.subtitle")}</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button className="sran-btn sran-btn-primary" onClick={() => setShowCreate(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            {t("tenancy.tenants.create")}
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* Stats */}
      {!loading && !error && items.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4" style={{ gap: 14, marginBottom: 20 }}>
          <StatCard value={stats.total} label={t("tenancy.tenants.statTotal")} icon={iconHouse} accent="#3b6cf6" delay={0} iconClass="tn-icon-pulse" />
          <StatCard value={stats.active} label={t("field.active")} icon={iconCheck} accent="#10b981" delay={50} iconClass="tn-icon-glow" />
          <StatCard value={stats.deleted} label={t("tenancy.status.deleted")} icon={iconX} accent="#ef4444" delay={100} iconClass="tn-icon-swing" />
          <StatCard value={stats.regions} label={t("tenancy.tenants.statRegions")} icon={iconGlobe} accent="#8b5cf6" delay={150} iconClass="tn-icon-pulse" />
        </div>
      )}

      {/* Main card */}
      <div className="tn-card" style={{ animationDelay: "0.25s", background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflow: "hidden" }}>
        {loading ? (
          <SkeletonTable rows={6} cols={5} />
        ) : error ? (
          <div style={{ padding: 20 }}><ErrorState message={error} onRetry={refetch} /></div>
        ) : items.length === 0 ? (
          <div style={{ padding: 20 }}><EmptyState message={t("tenancy.tenants.empty")} icon={<Icon name="building" size={22} />} action={<button onClick={() => setShowCreate(true)} style={{ padding: "8px 16px", fontSize: 13, fontWeight: 600, fontFamily: "inherit", borderRadius: 8, border: "none", background: T.accent, color: "white", cursor: "pointer" }}>{t("tenancy.tenants.emptyAction")}</button>} /></div>
        ) : (
          <>
            {/* Toolbar */}
            <div style={{ padding: "10px 20px", borderBottom: "1px solid var(--border-default)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ position: "relative", flex: "0 1 280px", minWidth: 160 }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("tenancy.tenants.searchPlaceholder")}
                  style={{ width: "100%", padding: "6px 24px 6px 30px", fontSize: 13, fontFamily: "inherit", border: `1.5px solid ${T.bl}`, borderRadius: 6, background: "var(--bg-base)", color: T.tp, outline: "none", transition: "border-color 120ms, box-shadow 120ms, background 120ms" }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.18)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
                />
                {search && <button onClick={() => setSearch("")} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: T.tt, fontSize: 12, padding: 2 }}><Icon name="x" size={12} /></button>}
              </div>

              <span style={{ width: 1, height: 24, background: T.bl, flexShrink: 0 }} />

              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                {[
                  { key: null, label: t("field.all"), count: items.length, dot: null },
                  { key: "active", label: t("field.active"), count: activeCount, dot: "#10b981" },
                  { key: "deleted", label: t("tenancy.status.deleted"), count: deletedCount, dot: "#ef4444" },
                ].map((f) => {
                  const active = statusFilter === f.key;
                  return (
                    <button key={f.label} onClick={() => setStatusFilter(f.key)} style={{
                      display: "inline-flex", alignItems: "center", gap: 5,
                      padding: "4px 10px", fontSize: 11, fontWeight: active ? 600 : 500,
                      borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
                      border: active ? `1.5px solid ${T.accent}` : "1.5px solid transparent",
                      background: active ? "rgba(59,108,246,0.06)" : "transparent",
                      color: active ? T.accent : T.tt, transition: "all 120ms",
                    }}>
                      {f.dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: f.dot, opacity: active ? 1 : 0.5, flexShrink: 0 }} />}
                      {f.label} <span style={{ fontWeight: 700, fontSize: 10 }}>{f.count}</span>
                    </button>
                  );
                })}
              </div>

              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 12, fontWeight: 500, color: T.tt }}>
                {t(filtered.length === 1 ? "tenancy.tenants.countOne" : "tenancy.tenants.count", { n: filtered.length })}
              </span>
            </div>

            {/* Table */}
            <div style={{ overflowX: "auto" }}>
              <table className="tn-table" style={{ minWidth: 700 }}>
                <thead><tr>
                  <th style={{ paddingLeft: 20 }}>{t("field.tenant")}</th>
                  <th>{t("tenancy.field.region")}</th>
                  <th style={{ textAlign: "center", width: 110 }}>{t("field.status")}</th>
                  <th style={{ width: 120 }}>{t("field.created")}</th>
                  <th style={{ width: 140 }}></th>
                </tr></thead>
                <tbody>
                  {filtered.map((row, i) => {
                    const ac = avatarColor(row.name || "");
                    const st = STATUS[row.status] || STATUS.active;
                    const rs = regionStyle(row.region || "global");
                    const isDeleted = row.status === "deleted";
                    return (
                      <tr key={row.tenant_id} className="tn-row" style={{ animationDelay: `${0.3 + i * 0.03}s`, cursor: "pointer" }} onClick={() => openEdit(row)}>
                        {/* Tenant: avatar + name + slug */}
                        <td style={{ paddingLeft: 20 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                            <div style={{ width: 34, height: 34, borderRadius: 6, background: ac, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, fontWeight: 700, color: "white", flexShrink: 0 }}>
                              {(row.name || "?").charAt(0).toUpperCase()}
                            </div>
                            <div>
                              <div style={{ fontSize: 14, fontWeight: 600, color: T.tp, lineHeight: 1.3 }}>{row.name}</div>
                              <div style={{ fontFamily: T.mono, fontSize: "11.5px", color: T.tt, lineHeight: 1.2, marginTop: 1 }}>{row.slug}</div>
                            </div>
                          </div>
                        </td>
                        {/* Region */}
                        <td><Pill text={row.region || "global"} color={rs.color} bg={rs.bg} /></td>
                        {/* Status */}
                        <td style={{ textAlign: "center" }}>
                          <span style={{
                            display: "inline-flex", alignItems: "center", gap: 6,
                            padding: "3px 10px", borderRadius: 100,
                            fontSize: "11.5px", fontWeight: 600,
                            color: st.color, background: st.bg,
                          }}>
                            <span style={{
                              width: 6, height: 6, borderRadius: "50%", background: st.dot, flexShrink: 0,
                              animation: st.glow ? "dotGlow 2s ease-in-out infinite" : "none",
                            }} />
                            {row.status}
                          </span>
                        </td>
                        {/* Created */}
                        <td>
                          <span style={{ fontFamily: T.mono, fontSize: "12.5px", color: T.ts }}>
                            {row.created_at ? new Date(row.created_at).toLocaleDateString() : "—"}
                          </span>
                        </td>
                        {/* Actions */}
                        <td>
                          <div className="tn-acts" style={{ display: "flex", gap: 6, justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()}>
                            {isDeleted ? (
                              <button onClick={() => setRestoreTarget(row)} style={{
                                display: "inline-flex", alignItems: "center", gap: 4,
                                padding: "6px 12px", fontSize: 12, fontWeight: 500, fontFamily: "inherit",
                                borderRadius: 6, border: "1px solid rgba(16,185,129,0.3)", background: "transparent",
                                color: "#059669", cursor: "pointer", transition: "background 100ms",
                              }}
                                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(16,185,129,0.06)"; }}
                                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                              >
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
                                {t("tenancy.action.restore")}
                              </button>
                            ) : row.slug !== "demo" ? (
                              <>
                                <button style={{
                                  display: "inline-flex", alignItems: "center", gap: 4,
                                  padding: "6px 12px", fontSize: 12, fontWeight: 500, fontFamily: "inherit",
                                  borderRadius: 6, border: `1px solid ${T.bl}`, background: "transparent",
                                  color: T.ts, cursor: "pointer", transition: "all 100ms",
                                }}
                                  onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(59,108,246,0.06)"; e.currentTarget.style.color = T.accent; e.currentTarget.style.borderColor = "rgba(59,108,246,0.3)"; }}
                                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = T.ts; e.currentTarget.style.borderColor = T.bl; }}
                                  onClick={() => openEdit(row)}
                                >
                                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                                  {t("action.edit")}
                                </button>
                                <button style={{
                                  display: "inline-flex", alignItems: "center", gap: 4,
                                  padding: "6px 12px", fontSize: 12, fontWeight: 500, fontFamily: "inherit",
                                  borderRadius: 6, border: "1px solid rgba(239,68,68,0.3)", background: "transparent",
                                  color: "#dc2626", cursor: "pointer", transition: "background 100ms",
                                }}
                                  onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(239,68,68,0.06)"; }}
                                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                                  onClick={() => setDeleteTarget(row)}
                                >{t("action.delete")}</button>
                              </>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Delete confirm */}
      <ConfirmModal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} title={t("tenancy.tenants.deleteTitle")} message={t("tenancy.tenants.deleteMessage", { name: deleteTarget?.name ?? "" })} confirmLabel={t("tenancy.tenants.deleteTitle")} loading={deleting} />

      {/* Restore confirm */}
      <ConfirmModal open={!!restoreTarget} onClose={() => setRestoreTarget(null)} onConfirm={handleRestore} title={t("tenancy.tenants.restoreTitle")} message={t("tenancy.tenants.restoreMessage", { name: restoreTarget?.name ?? "" })} confirmLabel={t("tenancy.action.restore")} loading={restoring} />

      {/* Edit modal — name + region + status editable. slug and
          tenant_id are immutable (URL-stable identifiers) so they
          render as read-only context fields. */}
      <Modal
        open={!!editTarget && !deleteTarget}
        onClose={() => { setEditTarget(null); setEditErrors({}); }}
        title={t("tenancy.tenants.editTitle")}
        footer={
          <>
            <button className="sran-btn sran-btn-secondary" onClick={() => { setEditTarget(null); setEditErrors({}); }} disabled={updating}>{t("action.cancel")}</button>
            <button className="sran-btn sran-btn-primary" onClick={handleUpdate} disabled={updating}>{updating ? t("action.saving") : t("action.save")}</button>
          </>
        }
      >
        {editTarget && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
              <div className="sran-detail-field"><div className="sran-detail-label">{t("tenancy.tenants.slugImmutable")}</div><div className="sran-detail-value"><code>{editTarget.slug}</code></div></div>
              <div className="sran-detail-field"><div className="sran-detail-label">{t("tenancy.field.tenantId")}</div><div className="sran-detail-value"><code style={{ fontSize: 11 }}>{editTarget.tenant_id}</code></div></div>
            </div>
            <div>
              <label className="sran-label">{t("field.name")} *</label>
              <input
                className={`sran-input ${editErrors.name ? "sran-input-error" : ""}`}
                value={editForm.name}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                placeholder={t("tenancy.tenants.namePlaceholder")}
                maxLength={64}
              />
              {editErrors.name && <div className="sran-field-error">{t(editErrors.name)}</div>}
              <div className="sran-char-count">{editForm.name.length}/64</div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
              <div>
                <label className="sran-label">{t("tenancy.field.region")}</label>
                <select className="sran-input" value={editForm.region} onChange={(e) => setEditForm({ ...editForm, region: e.target.value })}>
                  <option value="global">Global</option>
                  <option value="us">US</option>
                  <option value="eu">EU</option>
                </select>
              </div>
              <div>
                <label className="sran-label">{t("field.status")}</label>
                <select className="sran-input" value={editForm.status} onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}>
                  <option value="active">active</option>
                  <option value="suspended">suspended</option>
                  <option value="deleted">deleted</option>
                </select>
              </div>
            </div>
            <div>
              <label className="sran-label">{t("tenancy.tenants.creditMarkup")}</label>
              <input
                type="number"
                className={`sran-input ${editErrors.credit_markup ? "sran-input-error" : ""}`}
                value={editForm.credit_markup}
                onChange={(e) => setEditForm({ ...editForm, credit_markup: e.target.value })}
                step={0.1}
                min={1.0}
                max={5.0}
              />
              {editErrors.credit_markup && <div className="sran-field-error">{t(editErrors.credit_markup)}</div>}
              <div className="sran-char-count">{t("tenancy.tenants.creditMarkupHint")}</div>
            </div>
            <div className="sran-detail-field"><div className="sran-detail-label">{t("field.created")}</div><div className="sran-detail-value">{editTarget.created_at ? new Date(editTarget.created_at).toLocaleString() : "—"}</div></div>
          </div>
        )}
      </Modal>

      {/* Create modal */}
      <Modal open={showCreate} onClose={() => { setShowCreate(false); setErrors({}); }} title={t("tenancy.tenants.create")} footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={() => { setShowCreate(false); setErrors({}); }}>{t("action.cancel")}</button>
          <button className="sran-btn sran-btn-primary" onClick={handleCreate} disabled={saving}>{saving ? t("action.creating") : t("action.create")}</button>
        </>
      }>
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label className="sran-label">{t("field.name")} *</label>
            <input className={`sran-input ${errors.name ? "sran-input-error" : ""}`} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t("tenancy.tenants.namePlaceholder")} maxLength={64} />
            {errors.name && <div className="sran-field-error">{t(errors.name)}</div>}
            <div className="sran-char-count">{form.name.length}/64</div>
          </div>
          <div>
            <label className="sran-label">{t("tenancy.field.slug")} *</label>
            <input className={`sran-input ${errors.slug ? "sran-input-error" : ""}`} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="acme-corp" maxLength={64} />
            {errors.slug && <div className="sran-field-error">{t(errors.slug)}</div>}
            <div className="sran-char-count">{form.slug.length}/64</div>
          </div>
          <div>
            <label className="sran-label">{t("tenancy.field.region")}</label>
            <select className="sran-input" value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })}>
              <option value="global">Global</option><option value="us">US</option><option value="eu">EU</option>
            </select>
          </div>
        </div>
      </Modal>
    </div>
  );
}
