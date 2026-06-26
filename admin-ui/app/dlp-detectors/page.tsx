"use client";

// Admin-configurable DLP detectors management (Sprint 6, G).
// List + status filter + create panel + detail (edit form + audit timeline)
// with lifecycle transitions. Calls /admin/dlp/custom-detectors.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import Modal from "../../components/ui/Modal";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { Icon } from "../../components/ui/Icon";
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
// Map lifecycle status → shared design-system badge variant (theme-aware).
const STATUS_BADGE: Record<string, string> = {
  active: "sran-badge-success",
  testing: "sran-badge-warning",
  draft: "sran-badge-neutral",
  disabled: "sran-badge-error",
};

export default function DlpDetectorsPage() {
  const [rows, setRows] = useState<Detector[]>([]);
  const [tab, setTab] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Detector | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [disableTarget, setDisableTarget] = useState<Detector | null>(null);
  const [disableReason, setDisableReason] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Detector | null>(null);
  const [deleteReason, setDeleteReason] = useState("");

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
    if (verb === "disable") {
      setDisableReason("");
      setDisableTarget(d);
      return;
    }
    try {
      await api.post(`${BASE}/${d.id}/${verb}`, {});
      await load();
      if (selected?.id === d.id) openDetail(d);
    } catch (e) {
      setError(e instanceof ApiError ? (e.body?.error?.message ?? "action failed") : "action failed");
    }
  }

  async function doDisable() {
    if (!disableTarget) return;
    const d = disableTarget;
    const reason = disableReason.trim();
    if (!reason) return;
    try {
      await api.post(`${BASE}/${d.id}/disable`, { reason });
      setDisableTarget(null);
      await load();
      if (selected?.id === d.id) openDetail(d);
    } catch (e) {
      setError(e instanceof ApiError ? (e.body?.error?.message ?? "action failed") : "action failed");
    }
  }

  function remove(d: Detector) {
    setDeleteReason("");
    setDeleteTarget(d);
  }

  async function doDelete() {
    if (!deleteTarget) return;
    const d = deleteTarget;
    const reason = deleteReason.trim();
    if (!reason) return;
    try {
      await api.delete(`${BASE}/${d.id}?reason=${encodeURIComponent(reason)}`);
      setDeleteTarget(null);
      setSelected(null);
      await load();
    } catch {
      setError("delete failed");
    }
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
          {error && <div style={{ color: "var(--color-status-error)", fontSize: 13, marginBottom: 12 }}>{error}</div>}
          {loading ? (
            <SkeletonTable rows={6} cols={6} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<Icon name="shield" size={22} />}
              message={tab === "all" ? "No custom detectors yet" : `No ${tab} detectors`}
              action={
                tab === "all" ? (
                  <button style={primaryBtn} onClick={() => { setCreating(true); setSelected(null); }}>
                    Create a detector
                  </button>
                ) : undefined
              }
            />
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
                      <span className={`sran-badge ${STATUS_BADGE[d.status] ?? "sran-badge-neutral"}`}>
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
                        <button style={dangerBtn} onClick={() => remove(d)}>Delete</button>
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

      <Modal
        open={!!disableTarget}
        onClose={() => setDisableTarget(null)}
        title="Disable detector"
        footer={
          <>
            <button style={miniBtn} onClick={() => setDisableTarget(null)}>Cancel</button>
            <button
              style={{ ...primaryBtn, opacity: disableReason.trim() ? 1 : 0.5 }}
              disabled={!disableReason.trim()}
              onClick={() => void doDisable()}
            >
              Disable
            </button>
          </>
        }
      >
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          Reason for disabling{disableTarget ? ` "${disableTarget.name}"` : ""}:
        </label>
        <input
          className="sran-input"
          autoFocus
          value={disableReason}
          onChange={(e) => setDisableReason(e.target.value)}
          placeholder="Reason for disabling"
          style={{ width: "100%" }}
        />
      </Modal>

      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete detector"
        footer={
          <>
            <button style={miniBtn} onClick={() => setDeleteTarget(null)}>Cancel</button>
            <button
              className="sran-btn sran-btn-danger"
              disabled={!deleteReason.trim()}
              onClick={() => void doDelete()}
            >
              Delete
            </button>
          </>
        }
      >
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 12 }}>
          Delete &quot;{deleteTarget?.name}&quot;? This is permanent.
        </p>
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          Reason:
        </label>
        <input
          className="sran-input"
          autoFocus
          value={deleteReason}
          onChange={(e) => setDeleteReason(e.target.value)}
          placeholder="Reason for deleting"
          style={{ width: "100%" }}
        />
      </Modal>
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
const dangerBtn: React.CSSProperties = {
  padding: "4px 10px", fontSize: 12, fontWeight: 600, borderRadius: 5, cursor: "pointer",
  border: "1px solid var(--color-status-error)", background: "var(--color-status-error-bg)",
  color: "var(--color-status-error)", marginLeft: 4,
};
