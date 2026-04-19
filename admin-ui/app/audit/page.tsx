"use client";

import { useState, useMemo } from "react";
import { Outfit } from "next/font/google";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";

const gFont = Outfit({ subsets: ["latin"], weight: ["400", "500", "600", "700"] });

/* ── GCP Tokens ───────────────────────────────────────────────────── */

const G = {
  bg: "#f8f9fa", card: "#ffffff", surfaceBg: "#f1f3f4",
  border: "#dadce0", borderLight: "#e8eaed",
  tp: "#202124", ts: "#5f6368", tt: "#80868b",
  blue: "#1a73e8", blueHover: "#1967d2", blueBg: "#e8f0fe",
  green: "#1e8e3e", greenBg: "#e6f4ea",
  orange: "#e37400", orangeBg: "#fef7e0",
  red: "#d93025", redBg: "#fce8e6",
  mono: "'JetBrains Mono','Google Sans Mono','Fira Code',monospace",
  dark: "#1e1e2e",
};

const CSS = `
@keyframes gcpFadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.gcp-anim{animation:gcpFadeUp 0.3s ease-out both}
.gcp-table{width:100%;border-collapse:collapse}
.gcp-table th{text-align:left;padding:8px 16px;font-weight:500;font-size:11px;color:${G.ts};background:${G.surfaceBg};border-bottom:1px solid ${G.border};white-space:nowrap}
.gcp-table td{padding:9px 16px;border-bottom:1px solid ${G.borderLight};vertical-align:middle;font-size:13px}
.gcp-table tbody tr{transition:background 60ms}
.gcp-table tbody tr:hover{background:rgba(26,115,232,0.04)}
.gcp-table tbody tr:last-child td{border-bottom:none}
`;

/* ── Severity / Policy chips ──────────────────────────────────────── */

function SevChip({ status }: { status: string }) {
  const s = status === "success" ? { c: G.green, bg: G.greenBg, t: "OK" }
    : status === "blocked" ? { c: G.red, bg: G.redBg, t: "ERROR" }
    : { c: G.orange, bg: G.orangeBg, t: "WARNING" };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 8px", borderRadius: 4, fontSize: 12, fontFamily: G.mono, fontWeight: 500, color: s.c, background: s.bg }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.c }} />
      {s.t}
    </span>
  );
}

function PolChip({ action }: { action: string }) {
  const s = action === "allow" ? { c: G.green, bg: G.greenBg }
    : action === "block" ? { c: G.red, bg: G.redBg }
    : { c: G.orange, bg: G.orangeBg };
  return <span style={{ display: "inline-block", padding: "2px 8px", borderRadius: 4, fontSize: 12, fontFamily: G.mono, fontWeight: 500, color: s.c, background: s.bg }}>{action}</span>;
}

function Label({ k, v }: { k: string; v: string }) {
  return (
    <span style={{ display: "inline-flex", background: G.surfaceBg, borderRadius: 4, padding: "2px 6px", fontSize: 11, fontFamily: G.mono, gap: 3 }}>
      <span style={{ color: G.tt }}>{k}:</span>
      <span style={{ color: G.ts }}>{v}</span>
    </span>
  );
}

/* ── JSON highlight (Catppuccin Mocha) ────────────────────────────── */

function JsonBlock({ data }: { data: any }) {
  const json = JSON.stringify(data, null, 2);
  const html = json
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"([^"]+)":/g, '<span style="color:#89b4fa">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span style="color:#a6e3a1">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span style="color:#fab387">$1</span>')
    .replace(/: (true|false|null)/g, ': <span style="color:#f38ba8">$1</span>');
  return <pre style={{ background: G.dark, borderRadius: 6, padding: "14px 16px", margin: 0, fontFamily: G.mono, fontSize: 12, lineHeight: 1.7, color: "#6c7086", overflowX: "auto", maxHeight: 300 }} dangerouslySetInnerHTML={{ __html: html }} />;
}

/* ═══════════════════════════════════════════════════════════════════ */
/* PAGE                                                                */
/* ═══════════════════════════════════════════════════════════════════ */

export default function AuditPage() {
  const { data, loading, error, refetch } = useApi<{ items: any[] }>("/admin/requests");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, any>>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [downloading, setDownloading] = useState(false);
  const { toast } = useToast();

  const items = data?.items ?? [];

  const filtered = useMemo(() => {
    let r = items;
    if (statusFilter === "success") r = r.filter((x) => x.status === "success");
    else if (statusFilter === "masked") r = r.filter((x) => x.policy_action === "mask");
    else if (statusFilter === "error") r = r.filter((x) => x.status !== "success");
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((x) => x.request_id?.toLowerCase().includes(q) || x.model?.toLowerCase().includes(q) || x.provider?.toLowerCase().includes(q));
    }
    return r;
  }, [items, statusFilter, search]);

  const paged = useMemo(() => filtered.slice((page - 1) * pageSize, page * pageSize), [filtered, page, pageSize]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));

  const allowedCount = items.filter((x) => x.policy_action === "allow").length;
  const maskedCount = items.filter((x) => x.policy_action === "mask").length;
  const blockedCount = items.filter((x) => x.policy_action === "block" || x.status === "blocked").length;

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    try {
      // limit=1000 lets the export carry well past the 100-row default
      // when the backend learns the param. Today's /admin/requests
      // ignores it and returns its server-side cap (currently 100) —
      // harmless and keeps the URL spec-compliant.
      const res = await api.get("/admin/requests?limit=1000") as { items?: any[] };
      let rows = res?.items ?? [];
      if (statusFilter === "success") rows = rows.filter((x) => x.status === "success");
      else if (statusFilter === "masked") rows = rows.filter((x) => x.policy_action === "mask");
      else if (statusFilter === "error") rows = rows.filter((x) => x.status !== "success");
      if (search) {
        const q = search.toLowerCase();
        rows = rows.filter((x) => x.request_id?.toLowerCase().includes(q) || x.model?.toLowerCase().includes(q) || x.provider?.toLowerCase().includes(q));
      }

      // The list endpoint doesn't carry dlp_findings (only the
      // /admin/requests/{id} detail endpoint does). For rows the user
      // already expanded in this session, we have the detail cached on
      // ``details`` and can read masked detector names from there. For
      // the rest the cell is left empty — better than firing N+1 detail
      // requests on every Download click.
      const maskedFieldsFor = (r: any): string => {
        const det = details[r.request_id];
        const findings = det?.dlp_findings;
        if (!Array.isArray(findings) || findings.length === 0) return "";
        const names = new Set<string>();
        for (const f of findings) {
          if (f && f.action === "mask" && typeof f.detector === "string") {
            names.add(f.detector);
          }
        }
        return Array.from(names).join(";");
      };

      const header = [
        "request_id",
        "timestamp",
        "provider",
        "model",
        "policy",
        "latency_ms",
        "status",
        "masked_fields",
      ];
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
          maskedFieldsFor(r),
        ].map(esc).join(","));
      }
      const csv = lines.join("\n");

      const today = new Date().toISOString().slice(0, 10);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-log-${today}.csv`;
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

  async function toggleExpand(r: any) {
    const id = r.request_id;
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (!details[id]) {
      try { const d = await api.get(`/admin/requests/${id}`); setDetails((p) => ({ ...p, [id]: d })); }
      catch { setDetails((p) => ({ ...p, [id]: null })); }
    }
  }

  return (
    <div className={gFont.className} style={{ background: G.bg, margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{CSS}</style>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 14, marginBottom: 16, borderBottom: `1px solid ${G.border}` }}>
        <div>
          <h1 className="sran-page-title">Audit Log</h1>
          <p className="sran-page-subtitle">Request audit trail and admin operation log</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={refetch} style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "6px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 4, border: `1px solid ${G.border}`, background: G.card, color: G.ts, cursor: "pointer" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
            Refresh
          </button>
          <button onClick={handleDownload} disabled={downloading} style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "6px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: 4, border: `1px solid ${G.border}`, background: G.card, color: G.ts, cursor: downloading ? "wait" : "pointer", opacity: downloading ? 0.65 : 1 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            {downloading ? "Preparing..." : "Download"}
          </button>
        </div>
      </div>

      {/* Info banner */}
      <div className="gcp-anim" style={{ background: G.blueBg, border: "1px solid rgba(26,115,232,0.12)", borderRadius: 8, padding: "12px 16px", marginBottom: 14, display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: G.ts }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={G.blue} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
        <span>Showing recent request audit records. <span style={{ fontWeight: 500, color: G.tp }}>Admin operation audit</span> (key create/revoke) is tracked in the admin_audit_log table.</span>
      </div>

      {/* Summary chips */}
      {!loading && !error && items.length > 0 && (
        <div className="gcp-anim" style={{ animationDelay: "0.03s", display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
          {[
            { label: "total records", count: items.length, dot: null },
            { label: "allowed", count: allowedCount, dot: G.green },
            { label: "masked", count: maskedCount, dot: G.orange },
            { label: "blocked", count: blockedCount, dot: G.red },
          ].map((c) => (
            <span key={c.label} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 14px", borderRadius: 16, background: G.card, border: `1px solid ${G.border}`, fontSize: 13 }}>
              {c.dot && <span style={{ width: 7, height: 7, borderRadius: "50%", background: c.dot }} />}
              <span style={{ fontFamily: G.mono, fontWeight: 500, color: G.tp }}>{c.count}</span>
              <span style={{ color: G.ts }}>{c.label}</span>
            </span>
          ))}
        </div>
      )}

      {/* Query bar */}
      {!loading && !error && items.length > 0 && (
        <div className="gcp-anim" style={{ animationDelay: "0.05s", background: G.card, border: `1px solid ${G.border}`, borderRadius: 8, boxShadow: "0 1px 2px rgba(0,0,0,0.05)", padding: "8px 8px 8px 14px", marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={G.tt} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="text" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Search by request ID, model, provider..."
            style={{ flex: 1, border: "none", outline: "none", fontSize: 13, fontFamily: G.mono, color: G.tp, background: "transparent", padding: "4px 0" }}
          />
          {search && <button onClick={() => { setSearch(""); setPage(1); }} style={{ background: "none", border: "none", cursor: "pointer", color: G.tt, fontSize: 14, padding: 4 }}>✕</button>}

          <span style={{ width: 1, height: 24, background: G.border, flexShrink: 0 }} />

          {[
            { key: null, label: "All", dot: null },
            { key: "success", label: "Success", dot: G.green },
            { key: "masked", label: "Masked", dot: G.orange },
            { key: "error", label: "Error", dot: G.red },
          ].map((f) => {
            const active = statusFilter === f.key;
            return (
              <button key={f.label} onClick={() => { setStatusFilter(f.key); setPage(1); }} style={{
                display: "inline-flex", alignItems: "center", gap: 4, padding: "4px 12px", fontSize: 12, fontWeight: 500,
                borderRadius: 16, cursor: "pointer", fontFamily: "inherit", border: active ? "1px solid rgba(26,115,232,0.2)" : "1px solid transparent",
                background: active ? G.blueBg : "transparent", color: active ? G.blue : G.ts, transition: "all 80ms",
              }}>
                {f.dot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: f.dot, opacity: active ? 1 : 0.6 }} />}
                {f.label}
              </button>
            );
          })}

          <button onClick={refetch} style={{ marginLeft: 4, padding: "6px 14px", fontSize: 13, fontWeight: 500, fontFamily: "inherit", borderRadius: "0 4px 4px 0", border: "none", background: G.blue, color: "white", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5 }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            Run
          </button>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div style={{ background: G.card, borderRadius: 8, border: `1px solid ${G.border}`, padding: 16 }}><SkeletonTable rows={10} cols={7} /></div>
      ) : error ? (
        <div style={{ background: G.card, borderRadius: 8, border: `1px solid ${G.border}`, padding: 16 }}><ErrorState message={error} onRetry={refetch} /></div>
      ) : items.length === 0 ? (
        <div style={{ background: G.card, borderRadius: 8, border: `1px solid ${G.border}`, padding: 16 }}><EmptyState message="No audit records" icon="☰" /></div>
      ) : (
        <div className="gcp-anim" style={{ animationDelay: "0.1s", background: G.card, borderRadius: 8, border: `1px solid ${G.border}`, boxShadow: "0 1px 2px rgba(0,0,0,0.05)", overflow: "hidden" }}>
          <div style={{ overflowX: "auto" }}>
            <table className="gcp-table" style={{ minWidth: 900 }}>
              <thead><tr>
                <th style={{ width: 36 }}></th>
                <th>Severity</th>
                <th>Timestamp</th>
                <th>Request ID</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Policy</th>
                <th>Latency</th>
                <th>Labels</th>
              </tr></thead>
              <tbody>
                {paged.map((r) => {
                  const isExp = expanded === r.request_id;
                  const det = details[r.request_id];
                  return (
                    <>{/* Row */}
                    <tr key={r.request_id} onClick={() => toggleExpand(r)} style={{ cursor: "pointer", background: isExp ? G.blueBg : undefined }}>
                      <td style={{ textAlign: "center", padding: "9px 8px" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 24, height: 24, borderRadius: "50%", color: isExp ? G.blue : G.tt, transition: "color 0.2s" }}>
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: isExp ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.2s ease" }}>
                            <polyline points="6 9 12 15 18 9"/>
                          </svg>
                        </span>
                      </td>
                      <td><SevChip status={r.status} /></td>
                      <td style={{ fontFamily: G.mono, fontSize: 12, color: G.tt }}>{r.created_at ? new Date(r.created_at).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }) : "—"}</td>
                      <td style={{ fontFamily: G.mono, fontSize: 12, color: G.tp, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.request_id}</td>
                      <td style={{ fontSize: 13, color: G.tp, textTransform: "capitalize" }}>{r.provider || "—"}</td>
                      <td style={{ fontFamily: G.mono, fontSize: 12, color: G.tp }}>{r.model || "—"}</td>
                      <td><PolChip action={r.policy_action || "allow"} /></td>
                      <td style={{ fontFamily: G.mono, fontSize: 12, color: G.ts }}>{r.latency_ms || 0}ms</td>
                      <td>
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {r.route_strategy && <Label k="route" v={r.route_strategy} />}
                          {r.status_code && <Label k="code" v={String(r.status_code)} />}
                          {!r.route_strategy && !r.status_code && <span style={{ color: G.tt, fontSize: 12 }}>—</span>}
                        </div>
                      </td>
                    </tr>
                    {/* Expanded detail */}
                    {isExp && (
                      <tr key={`${r.request_id}-detail`}><td colSpan={9} style={{ padding: "12px 16px", background: G.blueBg, borderBottom: `1px solid ${G.borderLight}` }}>
                        <div style={{ background: G.card, borderRadius: 8, border: `1px solid ${G.border}`, padding: "20px 24px" }}>
                          {!det ? (
                            <div style={{ textAlign: "center", padding: "20px 0", color: G.tt, fontSize: 13 }}>Loading…</div>
                          ) : (
                            <>
                              {/* Summary grid */}
                              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px 20px", marginBottom: 20 }}>
                                {[
                                  ["Request ID", <span key="id" style={{ fontFamily: G.mono, fontSize: 12, wordBreak: "break-all" as const }}>{det.request_id}</span>],
                                  ["Provider", <span key="p" style={{ textTransform: "capitalize" as const }}>{det.provider}</span>],
                                  ["Model", <span key="m" style={{ fontFamily: G.mono, fontSize: 12 }}>{det.model}</span>],
                                  ["Status", <SevChip key="s" status={det.status} />],
                                  ["Status Code", det.error_code || "200"],
                                  ["DLP Policy", <PolChip key="d" action={det.policy_action} />],
                                  ["Latency", <span key="l" style={{ fontFamily: G.mono, fontSize: 12 }}>{det.latency_ms}ms</span>],
                                  ["Tokens", <span key="t" style={{ fontFamily: G.mono, fontSize: 12 }}>{det.prompt_tokens ?? "—"} in / {det.completion_tokens ?? "—"} out</span>],
                                  ["Route Strategy", det.route_strategy || "default"],
                                  ["Timestamp", <span key="ts" style={{ fontFamily: G.mono, fontSize: 12 }}>{det.created_at ? new Date(det.created_at).toLocaleString() : "—"}</span>],
                                ].map(([label, value]) => (
                                  <div key={label as string}>
                                    <div style={{ fontSize: 11, fontWeight: 500, textTransform: "uppercase", color: G.tt, marginBottom: 4 }}>{label}</div>
                                    <div style={{ fontSize: 13, fontWeight: 500, color: G.tp }}>{value}</div>
                                  </div>
                                ))}
                              </div>

                              {/* JSON */}
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                                <span style={{ fontSize: 12, fontWeight: 500, color: G.ts }}>Raw Payload</span>
                                <button onClick={(e) => {
                                  e.stopPropagation();
                                  navigator.clipboard.writeText(JSON.stringify(det, null, 2));
                                  toast("JSON copied to clipboard");
                                }} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 12, fontWeight: 500, color: G.blue }}>Copy JSON</button>
                              </div>
                              <JsonBlock data={{
                                request_id: det.request_id, provider: det.provider, model: det.model,
                                status: det.status, status_code: det.error_code || 200,
                                dlp_policy: det.policy_action, latency_ms: det.latency_ms,
                                tokens: { in: det.prompt_tokens ?? 0, out: det.completion_tokens ?? 0 },
                                route: det.route_strategy || "default",
                                timestamp: det.created_at,
                              }} />
                            </>
                          )}
                        </div>
                      </td></tr>
                    )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderTop: `1px solid ${G.border}`, fontSize: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, color: G.ts }}>
              Rows per page:
              <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} style={{ border: `1px solid ${G.border}`, borderRadius: 4, padding: "2px 4px", fontSize: 12, fontFamily: "inherit", color: G.tp, background: G.card, cursor: "pointer", outline: "none" }}>
                <option value={10}>10</option><option value={25}>25</option><option value={50}>50</option>
              </select>
            </div>
            <span style={{ fontFamily: G.mono, fontSize: 12, color: G.tt }}>
              {filtered.length > 0 ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, filtered.length)} of ${filtered.length}` : "0 results"}
            </span>
            <div style={{ display: "flex", gap: 4 }}>
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} style={{ width: 32, height: 32, borderRadius: "50%", border: "none", background: page <= 1 ? "transparent" : "transparent", color: page <= 1 ? G.borderLight : G.ts, cursor: page <= 1 ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", transition: "background 80ms" }}
                onMouseEnter={(e) => { if (page > 1) e.currentTarget.style.background = G.surfaceBg; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
              </button>
              <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} style={{ width: 32, height: 32, borderRadius: "50%", border: "none", background: "transparent", color: page >= totalPages ? G.borderLight : G.ts, cursor: page >= totalPages ? "not-allowed" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", transition: "background 80ms" }}
                onMouseEnter={(e) => { if (page < totalPages) e.currentTarget.style.background = G.surfaceBg; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
