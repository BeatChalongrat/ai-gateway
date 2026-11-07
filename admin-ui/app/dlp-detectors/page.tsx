"use client";

// Admin-configurable DLP detectors management (Sprint 6, G).
// List + status filter + create panel + detail (edit form + audit timeline)
// with lifecycle transitions. Calls /admin/dlp/custom-detectors.

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import Modal from "../../components/ui/Modal";
import PageHeader from "../../components/ui/PageHeader";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { Icon } from "../../components/ui/Icon";
import CustomDetectorForm, { DetectorInitial } from "../../components/dlp/CustomDetectorForm";
import AuditLogTimeline, { AuditEntry } from "../../components/dlp/AuditLogTimeline";
import { useT, type DictKey } from "../../lib/i18n";
import { DETECTION_TYPE, DLP_CATEGORY, codeWord } from "../../lib/codeWords";

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
// Lifecycle status is a human-facing word, not a wire identifier the
// operator types, so the label is localized while the value stays raw.
const STATUS_LABEL: Record<string, DictKey> = {
  all: "field.all",
  active: "infra.customDetectors.status.active",
  testing: "infra.customDetectors.status.testing",
  draft: "infra.customDetectors.status.draft",
  disabled: "infra.customDetectors.status.disabled",
};

export default function DlpDetectorsPage() {
  const t = useT();
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
    } catch {
      setError(t("infra.customDetectors.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [tab, t]);

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
      setError(
        e instanceof ApiError
          ? (e.body?.error?.message ?? t("infra.customDetectors.actionFailed"))
          : t("infra.customDetectors.actionFailed"),
      );
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
      setError(
        e instanceof ApiError
          ? (e.body?.error?.message ?? t("infra.customDetectors.actionFailed"))
          : t("infra.customDetectors.actionFailed"),
      );
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
      setError(t("infra.customDetectors.deleteFailed"));
    }
  }

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1200, margin: "0 auto" }}>
      <PageHeader
        title={t("infra.customDetectors.title")}
        subtitle={t("infra.customDetectors.subtitle")}
        action={
          <button style={primaryBtn} onClick={() => { setCreating(true); setSelected(null); }}>
            {t("infra.customDetectors.new")}
          </button>
        }
      />

      {/* status tabs — pill filters matching the /audit-log period buttons:
          active = semi-transparent accent (not solid fill). */}
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        {STATUS_TABS.map((s) => {
          const active = tab === s;
          return (
            <button key={s} onClick={() => setTab(s)} style={{
              padding: "5px 14px", fontSize: 12, fontWeight: 500, borderRadius: 100, cursor: "pointer",
              textTransform: "capitalize", fontFamily: "inherit",
              border: `1px solid ${active ? "rgb(30, 58, 95)" : "var(--border-default)"}`,
              background: active ? "rgba(30, 58, 95, 0.12)" : "var(--color-surface-primary)",
              color: active ? "var(--text-primary)" : "var(--text-secondary)",
              transition: "background 120ms, border-color 120ms",
            }}>{t(STATUS_LABEL[s])}</button>
          );
        })}
      </div>

      <div>
        {/* list */}
        <div>
          {error && <div style={{ color: "var(--color-status-error)", fontSize: 13, marginBottom: 12 }}>{error}</div>}
          {loading ? (
            <SkeletonTable rows={6} cols={6} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<Icon name="scan" size={22} />}
              message={
                tab === "all"
                  ? t("infra.customDetectors.empty")
                  : t("infra.customDetectors.emptyFiltered", { status: t(STATUS_LABEL[tab]) })
              }
              action={
                tab === "all" ? (
                  <button style={primaryBtn} onClick={() => { setCreating(true); setSelected(null); }}>
                    {t("infra.customDetectors.create")}
                  </button>
                ) : undefined
              }
            />
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase" }}>
                  <th style={th}>{t("field.name")}</th><th style={th}>{t("infra.policies.colCategory")}</th><th style={th}>{t("field.type")}</th>
                  <th style={th}>{t("field.status")}</th><th style={th}>{t("infra.customDetectors.colMatches")}</th><th style={th}>{t("field.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id} style={{ borderTop: "1px solid var(--border-default)", cursor: "pointer" }}
                    onClick={() => openDetail(d)}>
                    <td style={td}>{d.name}</td>
                    <td style={td}>{codeWord(DLP_CATEGORY, d.category, t)}</td>
                    <td style={td}>{codeWord(DETECTION_TYPE, d.detection_type, t)}</td>
                    <td style={td}>
                      <span className={`sran-badge ${STATUS_BADGE[d.status] ?? "sran-badge-neutral"}`}>
                        {STATUS_LABEL[d.status] ? t(STATUS_LABEL[d.status]) : d.status}
                      </span>
                    </td>
                    <td style={td}>{d.last_match_count}</td>
                    <td style={{ ...td }} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: 6 }}>
                        {d.status !== "active" && (
                          <button style={miniBtn} title={d.test_passed ? "" : t("infra.customDetectors.needsTests")}
                            onClick={() => transition(d, "activate")}>{t("infra.customDetectors.activate")}</button>
                        )}
                        {d.status !== "testing" && (
                          <button style={miniBtn} onClick={() => transition(d, "testing")}>{t("action.test")}</button>
                        )}
                        {d.status !== "disabled" && (
                          <button style={miniBtn} onClick={() => transition(d, "disable")}>{t("action.disable")}</button>
                        )}
                        <button style={dangerBtn} onClick={() => remove(d)}>{t("action.delete")}</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>

      {/* create / edit modal */}
      <Modal
        open={creating || !!selected}
        onClose={() => { setCreating(false); setSelected(null); }}
        maxWidth="640px"
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="scan" size={18} />
            {creating ? t("infra.customDetectors.newTitle") : selected?.name}
          </span>
        }
      >
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
                {t("infra.customDetectors.history")}
              </h3>
              <AuditLogTimeline entries={audit} />
            </div>
          </>
        ) : null}
      </Modal>

      <Modal
        open={!!disableTarget}
        onClose={() => setDisableTarget(null)}
        title={t("infra.customDetectors.disableTitle")}
        footer={
          <>
            <button style={miniBtn} onClick={() => setDisableTarget(null)}>{t("action.cancel")}</button>
            <button
              style={{ ...primaryBtn, opacity: disableReason.trim() ? 1 : 0.5 }}
              disabled={!disableReason.trim()}
              onClick={() => void doDisable()}
            >
              {t("action.disable")}
            </button>
          </>
        }
      >
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          {disableTarget
            ? t("infra.customDetectors.disableReason", { name: disableTarget.name })
            : t("infra.customDetectors.disableReasonPlain")}
        </label>
        <input
          className="sran-input"
          autoFocus
          value={disableReason}
          onChange={(e) => setDisableReason(e.target.value)}
          placeholder={t("infra.customDetectors.disableReasonPlaceholder")}
          style={{ width: "100%" }}
        />
      </Modal>

      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title={t("infra.customDetectors.deleteTitle")}
        footer={
          <>
            <button style={miniBtn} onClick={() => setDeleteTarget(null)}>{t("action.cancel")}</button>
            <button
              className="sran-btn sran-btn-danger"
              disabled={!deleteReason.trim()}
              onClick={() => void doDelete()}
            >
              {t("action.delete")}
            </button>
          </>
        }
      >
        <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 0, marginBottom: 12 }}>
          {t("infra.customDetectors.deleteConfirm", { name: deleteTarget?.name ?? "" })}
        </p>
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          {t("infra.customDetectors.reasonLabel")}
        </label>
        <input
          className="sran-input"
          autoFocus
          value={deleteReason}
          onChange={(e) => setDeleteReason(e.target.value)}
          placeholder={t("infra.customDetectors.deleteReasonPlaceholder")}
          style={{ width: "100%" }}
        />
      </Modal>
    </div>
  );
}

const th: React.CSSProperties = { padding: "6px 8px", fontWeight: 700 };
const td: React.CSSProperties = { padding: "8px", color: "var(--text-primary)" };
const primaryBtn: React.CSSProperties = {
  padding: "8px 18px", fontSize: 13, fontWeight: 500, borderRadius: 8, cursor: "pointer",
  border: "none", color: "#fff",
  background: "rgb(30, 58, 95)",
  boxShadow: "0 2px 8px rgba(30, 58, 95, 0.3)",
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
