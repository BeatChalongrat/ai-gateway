"use client";

// Vertical timeline of custom-detector audit entries (Sprint 6, G).
// Newest first. Shows actor, action, time, reason, and a compact before→after
// for pattern/status changes.

import { useT, type DictKey } from "../../lib/i18n";

export interface AuditEntry {
  id: string;
  action: string;
  detector_name: string;
  changed_by_user_id: string;
  changed_at: string;
  reason?: string | null;
  before_value?: Record<string, any> | null;
  after_value?: Record<string, any> | null;
}

const ACTION_COLOR: Record<string, string> = {
  create: "var(--color-status-success)",
  update: "var(--color-status-info)",
  activate: "var(--accent-violet)",
  move_to_testing: "var(--color-status-warning)",
  disable: "var(--color-status-error)",
  delete: "var(--color-status-error)",
};

// Only the actions the backend currently emits are translated; anything
// new falls back to the raw identifier so it is still legible (and
// greppable) rather than rendering blank.
const ACTION_LABEL: Record<string, DictKey> = {
  create: "ui.audit.action.create",
  update: "ui.audit.action.update",
  activate: "ui.audit.action.activate",
  move_to_testing: "ui.audit.action.moveToTesting",
  disable: "ui.audit.action.disable",
  delete: "ui.audit.action.delete",
};

function patternOf(v?: Record<string, any> | null): string | null {
  if (!v) return null;
  if (v.pattern && typeof v.pattern === "object") {
    return v.pattern.regex ?? JSON.stringify(v.pattern);
  }
  return null;
}

export default function AuditLogTimeline({ entries }: { entries: AuditEntry[] }) {
  const t = useT();

  if (!entries.length) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "12px 0" }}>
        {t("ui.audit.noHistory")}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {entries.map((e, i) => {
        const color = ACTION_COLOR[e.action] || "var(--text-secondary)";
        const beforeP = patternOf(e.before_value);
        const afterP = patternOf(e.after_value);
        const statusChange =
          e.before_value?.status && e.after_value?.status &&
          e.before_value.status !== e.after_value.status;
        const actionKey = ACTION_LABEL[e.action];
        const actionLabel = actionKey ? t(actionKey) : e.action.replace(/_/g, " ");
        return (
          <div key={e.id} style={{ display: "flex", gap: 12 }}>
            {/* rail */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <span style={{ width: 10, height: 10, borderRadius: 10, background: color, marginTop: 4 }} />
              {i < entries.length - 1 && (
                <span style={{ flex: 1, width: 2, background: "var(--border-default)" }} />
              )}
            </div>
            {/* body */}
            <div style={{ paddingBottom: 16, flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                <span style={{ color, textTransform: "capitalize" }}>
                  {actionLabel}
                </span>{" "}
                {t("ui.audit.by", { user: e.changed_by_user_id })}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {new Date(e.changed_at).toLocaleString()}
              </div>
              {statusChange && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
                  {t("ui.audit.statusChange", {
                    from: e.before_value!.status,
                    to: e.after_value!.status,
                  })}
                </div>
              )}
              {beforeP && afterP && beforeP !== afterP && (
                <div style={{ fontSize: 12, marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>
                  <div style={{ color: "var(--color-status-error)" }}>- {beforeP}</div>
                  <div style={{ color: "var(--color-status-success)" }}>+ {afterP}</div>
                </div>
              )}
              {e.reason && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2, fontStyle: "italic" }}>
                  {t("ui.audit.reason", { reason: e.reason })}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
