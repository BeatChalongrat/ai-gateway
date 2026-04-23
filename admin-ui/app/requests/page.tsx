"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { ThemeToggle } from "../../components/ThemeToggle";

const outfit = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });

// Theme-reactive tokens. Values resolve from CSS vars set on <html data-theme>
// so the whole page re-tints on theme toggle without re-rendering.
const T = {
  bg: "var(--bg-base)", card: "var(--bg-surface)",
  bl: "var(--border-default)", bm: "var(--border-default)",
  tp: "var(--text-primary)", ts: "var(--text-secondary)",
  th: "var(--text-secondary)", tt: "var(--text-muted)",
  accent: "var(--accent-primary)",
  green: "var(--accent-success)", red: "var(--accent-danger)",
  mono: "'JetBrains Mono','Fira Code',monospace",
  // Code-block background stays dark in both themes (syntax-highlighted output
  // reads poorly on a light card background).
  dark: "#1a1d27",
};

interface RequestDetail {
  request_id: string; provider: string; model: string; status: string;
  policy_action: string; latency_ms: number;
  prompt_tokens?: number; completion_tokens?: number; total_tokens?: number;
  estimated_cost_usd?: number; error_code?: string | null;
  created_at?: string | null;
  input_raw?: string | null; input_masked?: string | null;
  output_raw?: string | null; output_final?: string | null;
  dlp_findings?: any[] | null;
}

const CSS = `
@keyframes rqFadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes rqSlideIn{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:translateX(0)}}
@keyframes rqBlink{0%,100%{opacity:1}50%{opacity:0.3}}
.rq-anim{animation:rqFadeUp 0.35s ease-out both}
.rq-detail-in{animation:rqSlideIn 0.25s ease-out both}
.rq-row{transition:background 80ms}
.rq-row:hover{background:rgba(59,108,246,0.04)}
@media(max-width:1100px){.rq-detail-panel{display:none!important}.rq-split{grid-template-columns:1fr!important}}
`;

/* ── Sparkline ────────────────────────────────────────────────────── */

function Sparkline({ items }: { items: any[] }) {
  // Build 48 buckets (30min each over 24h) from request timestamps
  const now = Date.now();
  const buckets = useMemo(() => {
    const b = Array.from({ length: 48 }, () => ({ ok: 0, err: 0 }));
    items.forEach((r) => {
      if (!r.created_at) return;
      const age = now - new Date(r.created_at).getTime();
      const idx = 47 - Math.min(47, Math.floor(age / (30 * 60 * 1000)));
      if (r.status === "success") b[idx].ok++; else b[idx].err++;
    });
    return b;
  }, [items]);

  const maxH = Math.max(1, ...buckets.map((b) => b.ok + b.err));
  const totalOk = buckets.reduce((s, b) => s + b.ok, 0);
  const totalErr = buckets.reduce((s, b) => s + b.err, 0);

  return (
    <div className="rq-anim" style={{ background: T.card, borderRadius: 10, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", padding: "14px 20px", marginBottom: 14, display: "flex", alignItems: "center", gap: 16 }}>
      <span style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: T.tt, flexShrink: 0 }}>24h</span>
      <div style={{ display: "flex", gap: 2, alignItems: "flex-end", height: 28, flex: 1 }}>
        {buckets.map((b, i) => {
          const total = b.ok + b.err;
          const h = total > 0 ? Math.max(3, Math.round((total / maxH) * 28)) : 2;
          const hasErr = b.err > 0;
          return <div key={i} style={{ flex: 1, minWidth: 2, height: h, borderRadius: "2px 2px 0 0", background: hasErr ? T.red : total > 0 ? T.green : "var(--border-default)", opacity: 0.85, transition: "opacity 100ms" }}
            onMouseEnter={(e) => { e.currentTarget.style.opacity = "1"; }}
            onMouseLeave={(e) => { e.currentTarget.style.opacity = "0.85"; }}
          />;
        })}
      </div>
      <div style={{ display: "flex", gap: 14, flexShrink: 0, alignItems: "center" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.green }} />
          <span style={{ fontFamily: T.mono, fontSize: 13, fontWeight: 700, color: T.tp }}>{totalOk}</span>
          <span style={{ fontSize: 11, color: T.tt }}>ok</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.red }} />
          <span style={{ fontFamily: T.mono, fontSize: 13, fontWeight: 700, color: T.tp }}>{totalErr}</span>
          <span style={{ fontSize: 11, color: T.tt }}>err</span>
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 10px", borderRadius: 100, background: "rgba(16,185,129,0.08)", fontSize: 11, fontWeight: 600, color: "#059669" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.green, animation: "rqBlink 1.2s ease-in-out infinite" }} />
          Live
        </span>
      </div>
    </div>
  );
}

/* ── JSON highlight ───────────────────────────────────────────────── */

function JsonBlock({ data }: { data: any }) {
  const json = JSON.stringify(data, null, 2);
  // Simple syntax highlight
  const html = json
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"([^"]+)":/g, '<span style="color:rgba(255,255,255,0.35)">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span style="color:#10b981">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span style="color:#06b6d4">$1</span>')
    .replace(/: (true|false|null)/g, ': <span style="color:#f59e0b">$1</span>');

  return (
    <pre style={{
      background: T.dark, borderRadius: 8, padding: "14px 16px", margin: 0,
      fontFamily: T.mono, fontSize: "10.5px", lineHeight: 1.7, color: "rgba(255,255,255,0.7)",
      overflowX: "auto", maxHeight: 320,
    }} dangerouslySetInnerHTML={{ __html: html }} />
  );
}

// Small header strip for the JSON panel in the detail pane — label
// on the left, "Copy JSON" action on the right. Hoisted so the page
// body doesn't repeat the layout markup.
function JsonPanelHeader({ label, onCopy }: { label: string; onCopy: () => void }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
      <span style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt }}>{label}</span>
      <button
        onClick={(e) => { e.stopPropagation(); onCopy(); }}
        style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, fontWeight: 600, color: T.accent, padding: 0, fontFamily: "inherit" }}
      >
        Copy JSON
      </button>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

export default function RequestsPage() {
  const { data, loading, error, refetch } = useApi<{ items: any[] }>("/admin/requests");
  const [selected, setSelected] = useState<any | null>(null);
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [downloading, setDownloading] = useState(false);
  const { toast } = useToast();

  const items = data?.items ?? [];

  const filtered = useMemo(() => {
    let r = items;
    if (statusFilter === "ok") r = r.filter((x) => x.status === "success");
    else if (statusFilter === "error") r = r.filter((x) => x.status !== "success");
    else if (statusFilter === "masked") r = r.filter((x) => x.policy_action === "mask");
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((x) => x.request_id?.toLowerCase().includes(q) || x.model?.toLowerCase().includes(q) || x.provider?.toLowerCase().includes(q) || x.status?.toLowerCase().includes(q));
    }
    return r;
  }, [items, statusFilter, search]);

  const pageSize = 15;
  const paged = useMemo(() => filtered.slice((page - 1) * pageSize, page * pageSize), [filtered, page]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));

  async function selectRow(r: any) {
    setSelected(r);
    setDetail(null);
    setDetailLoading(true);
    try { const d = await api.get(`/admin/requests/${r.request_id}`); setDetail(d as RequestDetail); }
    catch { setDetail(null); }
    finally { setDetailLoading(false); }
  }

  const okCount = items.filter((x) => x.status === "success").length;
  const errCount = items.filter((x) => x.status !== "success").length;
  const allowedCount = items.filter((x) => x.policy_action === "allow").length;
  const maskedCount = items.filter((x) => x.policy_action === "mask").length;
  const blockedCount = items.filter((x) => x.policy_action === "block" || x.status === "blocked").length;

  // Client-side CSV export. The list endpoint doesn't carry detail
  // fields (input_masked, dlp_findings, etc.), so the export sticks
  // to what every row reliably has — consistent with what the old
  // /audit page exported before it was folded into this page.
  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    try {
      const res = (await api.get("/admin/requests?limit=1000")) as { items?: any[] };
      let rows = res?.items ?? [];
      if (statusFilter === "ok") rows = rows.filter((x) => x.status === "success");
      else if (statusFilter === "error") rows = rows.filter((x) => x.status !== "success");
      else if (statusFilter === "masked") rows = rows.filter((x) => x.policy_action === "mask");
      if (search) {
        const q = search.toLowerCase();
        rows = rows.filter((x) =>
          x.request_id?.toLowerCase().includes(q) ||
          x.model?.toLowerCase().includes(q) ||
          x.provider?.toLowerCase().includes(q) ||
          x.status?.toLowerCase().includes(q),
        );
      }

      const header = ["request_id", "timestamp", "provider", "model", "policy", "latency_ms", "status"];
      const esc = (v: any) => {
        const s = v == null ? "" : String(v);
        return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
      };
      const lines = [header.join(",")];
      for (const r of rows) {
        lines.push([
          r.request_id ?? "",
          r.created_at ?? "",
          r.provider ?? "",
          r.model ?? "",
          r.policy_action ?? "",
          r.latency_ms ?? "",
          r.status ?? "",
        ].map(esc).join(","));
      }
      const csv = lines.join("\n");

      const today = new Date().toISOString().slice(0, 10);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `requests-${today}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      toast(`Downloaded ${rows.length} record${rows.length === 1 ? "" : "s"}`);
    } catch (e: any) {
      toast(e?.body?.error?.message || "Download failed", "error");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className={outfit.className} style={{ background: T.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 30, fontWeight: 800, color: T.tp, letterSpacing: "-0.03em", lineHeight: 1.2, margin: 0 }}>Requests</h1>
          <p style={{ fontSize: 15, fontWeight: 500, color: T.ts, margin: "4px 0 0" }}>Gateway request log with routing, DLP, and provider details</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 8, border: `1px solid ${T.bl}`, background: T.card, color: T.ts, cursor: "pointer" }}>Last 24h</button>
          <button onClick={refetch} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 8, border: `1px solid ${T.bl}`, background: T.card, color: T.ts, cursor: "pointer" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
            Refresh
          </button>
          <button
            onClick={handleDownload}
            disabled={downloading || items.length === 0}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6, padding: "7px 14px",
              fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 8,
              border: `1px solid ${T.bl}`, background: T.card, color: T.ts,
              cursor: downloading ? "wait" : items.length === 0 ? "not-allowed" : "pointer",
              opacity: downloading || items.length === 0 ? 0.65 : 1,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            {downloading ? "Preparing…" : "Download"}
          </button>
          <ThemeToggle />
        </div>
      </div>

      {/* Stats chips — total / allowed / masked / blocked.
          Matches the summary row the old /audit page had before the
          two pages were folded together. */}
      {!loading && !error && items.length > 0 && (
        <div className="rq-anim" style={{ animationDelay: "0.02s", display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
          {[
            { label: "total records", count: items.length, dot: null },
            { label: "allowed", count: allowedCount, dot: T.green },
            { label: "masked", count: maskedCount, dot: "#f59e0b" },
            { label: "blocked", count: blockedCount, dot: T.red },
          ].map((c) => (
            <span key={c.label} style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "6px 14px", borderRadius: 16,
              background: T.card, border: `1px solid ${T.bl}`, fontSize: 13,
            }}>
              {c.dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: c.dot }} />}
              <span style={{ fontFamily: T.mono, fontWeight: 600, color: T.tp }}>{c.count}</span>
              <span style={{ color: T.ts }}>{c.label}</span>
            </span>
          ))}
        </div>
      )}

      {/* Sparkline */}
      {!loading && !error && items.length > 0 && <Sparkline items={items} />}

      {/* Filter bar */}
      {!loading && !error && items.length > 0 && (
        <div className="rq-anim" style={{ animationDelay: "0.05s", display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "0 1 320px", minWidth: 160 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Filter requests..."
              style={{ width: "100%", padding: "6px 24px 6px 30px", fontSize: 13, fontFamily: T.mono, border: `1.5px solid ${T.bl}`, borderRadius: 6, background: "var(--bg-base)", color: T.tp, outline: "none", transition: "border-color 120ms, box-shadow 120ms, background 120ms" }}
              onFocus={(e) => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.18)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
              onBlur={(e) => { e.currentTarget.style.borderColor = T.bl; e.currentTarget.style.boxShadow = "none"; e.currentTarget.style.background = "var(--bg-base)"; }}
            />
            {search && <button onClick={() => { setSearch(""); setPage(1); }} style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: T.tt, fontSize: 12, padding: 2 }}>✕</button>}
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {[
              { key: null, label: "All", count: items.length, dot: null },
              { key: "ok", label: "OK", count: okCount, dot: T.green },
              { key: "error", label: "Error", count: errCount, dot: T.red },
              { key: "masked", label: "Masked", count: maskedCount, dot: "#f59e0b" },
            ].map((f) => {
              const active = statusFilter === f.key;
              return (
                <button key={f.label} onClick={() => { setStatusFilter(f.key); setPage(1); }} style={{
                  display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 10px", fontSize: 11, fontWeight: active ? 600 : 500, borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
                  border: active ? `1.5px solid ${T.accent}` : "1.5px solid transparent",
                  background: active ? "rgba(59,108,246,0.06)" : "transparent", color: active ? T.accent : T.tt, transition: "all 120ms",
                }}>
                  {f.dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: f.dot, opacity: active ? 1 : 0.5 }} />}
                  {f.label} <span style={{ fontWeight: 700, fontSize: 10 }}>{f.count}</span>
                </button>
              );
            })}
          </div>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: T.mono, fontSize: 12, color: T.tt }}>{filtered.length} results</span>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}><SkeletonTable rows={10} cols={6} /></div>
      ) : error ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}><ErrorState message={error} onRetry={refetch} /></div>
      ) : items.length === 0 ? (
        <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, padding: 20 }}><EmptyState message="No requests recorded yet" icon="↗" /></div>
      ) : (
        <div className="rq-anim rq-split" style={{ animationDelay: "0.1s", display: "grid", gridTemplateColumns: "1fr 380px", gap: 16 }}>
          {/* Left: list */}
          <div style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <div style={{ flex: 1, overflowY: "auto" }}>
              {filtered.length === 0 ? (
                <div style={{ padding: "40px 20px", textAlign: "center", color: T.tt, fontSize: 13 }}>
                  No results match the current filter.
                </div>
              ) : null}
              {paged.map((r) => {
                const isOk = r.status === "success";
                const isSel = selected?.request_id === r.request_id;
                return (
                  <div key={r.request_id} className="rq-row" onClick={() => selectRow(r)} style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "12px 20px", cursor: "pointer",
                    borderBottom: `1px solid ${T.bl}`,
                    background: isSel ? "rgba(59,108,246,0.05)" : "transparent",
                    borderLeft: isSel ? `3px solid ${T.accent}` : "3px solid transparent",
                  }}>
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: isOk ? T.green : T.red, flexShrink: 0 }} />
                    <span style={{ fontFamily: T.mono, fontSize: "10.5px", color: T.tt, width: 76, flexShrink: 0 }}>
                      {r.created_at ? new Date(r.created_at).toLocaleTimeString() : "—"}
                    </span>
                    <span style={{ fontFamily: T.mono, fontSize: "11.5px", color: T.tp, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 500 }}>
                      {r.model}
                    </span>
                    <span style={{ fontSize: 11, color: T.tt, width: 70, flexShrink: 0, textTransform: "capitalize" }}>{r.provider}</span>
                    {r.policy_action && (
                      <span style={{
                        fontFamily: T.mono, fontSize: "9.5px", fontWeight: 600, padding: "2px 7px", borderRadius: 100,
                        color: r.policy_action === "allow" ? "#059669" : "#d97706",
                        background: r.policy_action === "allow" ? "rgba(16,185,129,0.08)" : "rgba(245,158,11,0.08)",
                      }}>{r.policy_action}</span>
                    )}
                    <span style={{ fontFamily: T.mono, fontSize: "10.5px", color: T.tt, width: 55, textAlign: "right", flexShrink: 0 }}>{r.latency_ms}ms</span>
                  </div>
                );
              })}
            </div>
            {/* Pagination */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 20px", borderTop: `1px solid ${T.bl}`, fontSize: 12 }}>
              <span style={{ fontFamily: T.mono, color: T.tt }}>
                {filtered.length > 0 ? `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, filtered.length)} of ${filtered.length}` : "0 results"}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button disabled={page <= 1} onClick={() => setPage(page - 1)} style={{ padding: "4px 10px", fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 6, border: `1px solid ${T.bl}`, background: T.card, color: page <= 1 ? T.tt : T.ts, cursor: page <= 1 ? "not-allowed" : "pointer", opacity: page <= 1 ? 0.5 : 1 }}>Prev</button>
                <span style={{ fontFamily: T.mono, fontSize: 12, color: T.tt, padding: "4px 6px" }}>{page}/{totalPages}</span>
                <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} style={{ padding: "4px 10px", fontSize: 12, fontWeight: 500, fontFamily: "inherit", borderRadius: 6, border: `1px solid ${T.bl}`, background: T.card, color: page >= totalPages ? T.tt : T.ts, cursor: page >= totalPages ? "not-allowed" : "pointer", opacity: page >= totalPages ? 0.5 : 1 }}>Next</button>
              </div>
            </div>
          </div>

          {/* Right: detail sidebar */}
          <div className="rq-detail-panel" style={{ background: T.card, borderRadius: 14, border: `1px solid ${T.bl}`, boxShadow: "0 1px 2px rgba(0,0,0,0.04)", padding: 24, position: "sticky", top: 20, alignSelf: "start", maxHeight: "calc(100vh - 60px)", overflowY: "auto" }}>
            {!selected ? (
              <div style={{ textAlign: "center", padding: "60px 20px", color: T.tt, fontSize: 13 }}>Select a request to view details</div>
            ) : detailLoading ? (
              <div style={{ textAlign: "center", padding: "60px 20px", color: T.tt, fontSize: 13 }}>Loading…</div>
            ) : detail ? (
              <div className="rq-detail-in" key={detail.request_id}>
                <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "1px", color: T.tt, marginBottom: 16 }}>Request Detail</div>

                {/* Request ID */}
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt, marginBottom: 3 }}>Request ID</div>
                  <div style={{ fontFamily: T.mono, fontSize: 12, color: T.tp, wordBreak: "break-all" }}>{detail.request_id}</div>
                </div>

                {/* Grid fields */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px 14px", marginBottom: 18 }}>
                  {[
                    ["Provider", <span key="p" style={{ textTransform: "capitalize" }}>{detail.provider}</span>],
                    ["Model", <span key="m" style={{ fontFamily: T.mono, fontSize: 12 }}>{detail.model}</span>],
                    ["Status", <span key="s" style={{ display: "inline-block", padding: "2px 8px", borderRadius: 100, fontSize: 11, fontWeight: 600, color: detail.status === "success" ? "#059669" : "#dc2626", background: detail.status === "success" ? "rgba(16,185,129,0.08)" : "rgba(239,68,68,0.08)" }}>{detail.status}</span>],
                    ["DLP Policy", <span key="d" style={{ display: "inline-block", padding: "2px 8px", borderRadius: 100, fontSize: 11, fontWeight: 600, color: detail.policy_action === "allow" ? "#059669" : "#d97706", background: detail.policy_action === "allow" ? "rgba(16,185,129,0.08)" : "rgba(245,158,11,0.08)" }}>{detail.policy_action}</span>],
                    ["Latency", <span key="l" style={{ fontFamily: T.mono, fontSize: 12 }}>{detail.latency_ms}ms</span>],
                    ["Tokens", <span key="t" style={{ fontFamily: T.mono, fontSize: 12 }}>{detail.prompt_tokens ?? "—"} in / {detail.completion_tokens ?? "—"} out</span>],
                  ].map(([label, value]) => (
                    <div key={label as string}>
                      <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt, marginBottom: 3 }}>{label}</div>
                      <div style={{ fontSize: 13, color: T.tp }}>{value}</div>
                    </div>
                  ))}
                </div>

                {/* Trace: 4 stages */}
                {(detail.input_raw || detail.input_masked || detail.output_raw || detail.output_final) && (
                  <div style={{ marginBottom: 18 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "1px", color: T.tt, marginBottom: 10 }}>Trace</div>
                    {[
                      { label: "User → Service", value: detail.input_raw, tone: "#3b6cf6" },
                      { label: "Service → AI", value: detail.input_masked, tone: "#f59e0b" },
                      { label: "AI → Service", value: detail.output_raw, tone: "#10b981" },
                      { label: "Service → User", value: detail.output_final, tone: "#6366f1" },
                    ].map((stage) => (
                      <div key={stage.label} style={{ marginBottom: 8 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                          <span style={{ width: 6, height: 6, borderRadius: "50%", background: stage.tone, flexShrink: 0 }} />
                          <span style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt }}>{stage.label}</span>
                        </div>
                        <pre style={{
                          background: "var(--bg-elevated)", border: `1px solid ${T.bl}`, borderRadius: 6,
                          padding: "8px 10px", margin: 0, fontFamily: T.mono, fontSize: "11px",
                          lineHeight: 1.5, color: T.tp, whiteSpace: "pre-wrap", wordBreak: "break-word",
                          maxHeight: 140, overflowY: "auto",
                        }}>{stage.value ?? "—"}</pre>
                      </div>
                    ))}
                    {detail.dlp_findings && detail.dlp_findings.length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: T.tt, marginBottom: 4 }}>DLP Findings ({detail.dlp_findings.length})</div>
                        <JsonBlock data={detail.dlp_findings} />
                      </div>
                    )}
                  </div>
                )}

                {/* JSON */}
                {(() => {
                  const payload = {
                    request_id: detail.request_id,
                    provider: detail.provider,
                    model: detail.model,
                    status: detail.status,
                    code: detail.error_code || null,
                    dlp: detail.policy_action,
                    latency_ms: detail.latency_ms,
                    tokens: { in: detail.prompt_tokens ?? 0, out: detail.completion_tokens ?? 0 },
                    time: detail.created_at,
                  };
                  return (
                    <>
                      <JsonPanelHeader
                        label="Raw Payload"
                        onCopy={() => {
                          navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
                          toast("JSON copied to clipboard");
                        }}
                      />
                      <JsonBlock data={payload} />
                    </>
                  );
                })()}
              </div>
            ) : (
              <div style={{ textAlign: "center", padding: "60px 20px", color: T.tt, fontSize: 13 }}>Failed to load details</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
