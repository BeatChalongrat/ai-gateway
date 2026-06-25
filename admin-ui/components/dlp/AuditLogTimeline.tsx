"use client";

// Vertical timeline of custom-detector audit entries (Sprint 6, G).
// Newest first. Shows actor, action, time, reason, and a compact before→after
// for pattern/status changes.

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
  create: "#059669",
  update: "#2563eb",
  activate: "#7c3aed",
  move_to_testing: "#b45309",
  disable: "#dc2626",
  delete: "#dc2626",
};

function patternOf(v?: Record<string, any> | null): string | null {
  if (!v) return null;
  if (v.pattern && typeof v.pattern === "object") {
    return v.pattern.regex ?? JSON.stringify(v.pattern);
  }
  return null;
}

export default function AuditLogTimeline({ entries }: { entries: AuditEntry[] }) {
  if (!entries.length) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "12px 0" }}>
        No history yet.
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
                  {e.action.replace(/_/g, " ")}
                </span>{" "}
                by {e.changed_by_user_id}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {new Date(e.changed_at).toLocaleString()}
              </div>
              {statusChange && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
                  status: {e.before_value!.status} → {e.after_value!.status}
                </div>
              )}
              {beforeP && afterP && beforeP !== afterP && (
                <div style={{ fontSize: 12, marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>
                  <div style={{ color: "#dc2626" }}>- {beforeP}</div>
                  <div style={{ color: "#059669" }}>+ {afterP}</div>
                </div>
              )}
              {e.reason && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2, fontStyle: "italic" }}>
                  reason: {e.reason}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
