"use client";

// Admin-configurable DLP detectors management (Sprint 6, G).
// List + status filter + create panel + detail (edit form + audit timeline)
// with lifecycle transitions. Calls /admin/dlp/custom-detectors.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import CustomDetectorForm, { DetectorInitial } from "../../components/dlp/CustomDetectorForm";
import AuditLogTimeline, { AuditEntry } from "../../components/dlp/AuditLogTimeline";

const BASE = "/admin/dlp/custom-detectors";

interface Detector {
  id: string;
  name: string;
  description?: string | null;
  category: string;
  detection_type: string;
  status: string;
  severity: string;
  default_action: string;
  test_passed: boolean;
  last_match_count: number;
  timeout_failures: number;
  version: number;
}

const STATUS_TABS = ["all", "active", "testing", "draft", "disabled"];
const STATUS_COLOR: Record<string, string> = {
  active: "#059669", testing: "#b45309", draft: "#6b7280", disabled: "#dc2626",
};

export default function DlpDetectorsPage() {
  const [rows, setRows] = useState<Detector[]>([]);
  const [tab, setTab] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Detector | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = tab === "all" ? "" : `?status=${tab}`;
      const data: any = await api.get(`${BASE}${q}`);
      setRows(data);
    } catch (e) {
      setError(e instanceof ApiError ? "Failed to load detectors" : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => { load(); }, [load]);

  const openDetail = useCallback(async (d: Detector) => {
    setSelected(d); setCreating(false);
    try {
      const a: any = await api.get(`${BASE}/${d.id}/audit-log`);
      setAudit(a);
    } catch { setAudit([]); }
  }, []);

  async function transition(d: Detector, verb: "activate" | "testing" | "disable") {
    try {
      if (verb === "disable") {
        const reason = window.prompt("Reason for disabling:");
        if (!reason) return;
        await api.post(`${BASE}/${d.id}/disable`, { reason });
      } else {
        await api.post(`${BASE}/${d.id}/${verb}`, {});
      }
      await load();
      if (selected?.id === d.id) openDetail(d);
    } catch (e) {
      alert(e instanceof ApiError ? (e.body?.error?.message ?? "action failed") : "action failed");
    }
  }

  async function remove(d: Detector) {
    const reason = window.prompt(`Delete "${d.name}"? This is permanent. Reason:`);
    if (!reason) return;
    if (!window.confirm(`Really delete ${d.name}?`)) return;
    try {
      await api.delete(`${BASE}/${d.id}?reason=${encodeURIComponent(reason)}`);
      setSelected(null);
      await load();
    } catch { alert("delete failed"); }
  }

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1200, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, color: "var(--text-primary)", margin: 0 }}>
            Custom DLP Detectors
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "4px 0 0" }}>
            Define detectors that run alongside the built-ins. Changes hot-reload instantly.
          </p>
        </div>
        <button style={primaryBtn} onClick={() => { setCreating(true); setSelected(null); }}>
          + New detector
        </button>
      </div>

      {/* status tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        {STATUS_TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: "6px 14px", fontSize: 13, fontWeight: 600, borderRadius: 6, cursor: "pointer",
            border: "1px solid var(--border-default)", textTransform: "capitalize",
            background: tab === t ? "var(--accent-primary)" : "transparent",
            color: tab === t ? "#fff" : "var(--text-secondary)",
          }}>{t}</button>
        ))}
      </div>

      <div style={{ display: "flex", gap: 20 }}>
        {/* list */}
        <div style={{ flex: 1 }}>
          {error && <div style={{ color: "#dc2626", fontSize: 13 }}>{error}</div>}
          {loading ? (
            <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</div>
          ) : rows.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: 13, padding: 20 }}>No detectors.</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase" }}>
                  <th style={th}>Name</th><th style={th}>Category</th><th style={th}>Type</th>
                  <th style={th}>Status</th><th style={th}>Matches</th><th style={th}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id} style={{ borderTop: "1px solid var(--border-default)", cursor: "pointer" }}
                    onClick={() => openDetail(d)}>
                    <td style={td}>{d.name}</td>
                    <td style={td}>{d.category}</td>
                    <td style={td}>{d.detection_type}</td>
                    <td style={td}>
                      <span style={{ color: STATUS_COLOR[d.status] ?? "var(--text-secondary)", fontWeight: 700 }}>
                        {d.status}
                      </span>
                    </td>
                    <td style={td}>{d.last_match_count}</td>
                    <td style={{ ...td }} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: 6 }}>
                        {d.status !== "active" && (
                          <button style={miniBtn} title={d.test_passed ? "" : "needs passing tests"}
                            onClick={() => transition(d, "activate")}>Activate</button>
                        )}
                        {d.status !== "testing" && (
                          <button style={miniBtn} onClick={() => transition(d, "testing")}>Test</button>
                        )}
                        {d.status !== "disabled" && (
                          <button style={miniBtn} onClick={() => transition(d, "disable")}>Disable</button>
                        )}
                        <button style={{ ...miniBtn, color: "#dc2626" }} onClick={() => remove(d)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* detail / create panel */}
        {(creating || selected) && (
          <div style={{ width: 460, border: "1px solid var(--border-default)", borderRadius: 10, padding: 18, background: "var(--bg-surface)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h2 style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
                {creating ? "New detector" : selected?.name}
              </h2>
              <button style={miniBtn} onClick={() => { setCreating(false); setSelected(null); }}>Close</button>
            </div>
            {creating ? (
              <CustomDetectorForm onCancel={() => setCreating(false)} onSaved={() => { setCreating(false); load(); }} />
            ) : selected ? (
              <>
                <CustomDetectorForm
                  initial={selected as DetectorInitial}
                  onCancel={() => setSelected(null)}
                  onSaved={() => { load(); openDetail(selected); }}
                />
                <div style={{ marginTop: 22 }}>
                  <h3 style={{ fontSize: 12, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", marginBottom: 10 }}>
                    History
                  </h3>
                  <AuditLogTimeline entries={audit} />
                </div>
              </>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "6px 8px", fontWeight: 700 };
const td: React.CSSProperties = { padding: "8px", color: "var(--text-primary)" };
const primaryBtn: React.CSSProperties = {
  padding: "8px 16px", fontSize: 13, fontWeight: 600, borderRadius: 6, cursor: "pointer",
  border: "none", background: "var(--accent-primary)", color: "#fff",
};
const miniBtn: React.CSSProperties = {
  padding: "4px 10px", fontSize: 12, fontWeight: 600, borderRadius: 5, cursor: "pointer",
  border: "1px solid var(--border-default)", background: "transparent", color: "var(--text-secondary)",
};
