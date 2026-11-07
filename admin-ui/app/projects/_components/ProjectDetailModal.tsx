"use client";

/**
 * Admin ProjectDetailModal (2026-04-24).
 *
 * Four sections stacked top-to-bottom:
 *   1. Overview — tenant / policy / created / creator / last activity
 *   2. Activity — requests (last 7d) / api_keys_count / members count
 *   3. Members — list with role + kebab (change role / remove), + Add
 *   4. Danger zone — Archive (soft-delete)
 *
 * Fetches /admin/projects/{id} on open, refetches after each mutation
 * so the surface is always in sync with the server. Reuses the admin
 * UI's existing Modal + ConfirmModal + Toast primitives.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import ConfirmModal from "../../../components/ui/ConfirmModal";
import { Icon } from "../../../components/ui/Icon";
import Modal from "../../../components/ui/Modal";
import { useToast } from "../../../components/ui/Toast";
import { api, ApiError } from "../../../lib/api";
import { useT, type DictKey } from "../../../lib/i18n";

/// The translate function handed down to the module-scope helpers below,
/// which render copy but sit outside the component that owns the hook.
type Translate = ReturnType<typeof useT>;

// A member's role as a person reads it; the value the API takes stays the identifier.
const ROLE_LABEL: Record<"admin" | "member" | "viewer", DictKey> = {
  admin: "tenancy.role.admin",
  member: "tenancy.role.member",
  viewer: "tenancy.role.viewer",
};

interface ProjectMemberRow {
  user_id: string;
  display_name: string | null;
  email: string;
  avatar_url: string | null;
  role: "admin" | "member" | "viewer";
  added_at: string | null;
}

interface Creator {
  user_id: string;
  display_name: string | null;
  email: string | null;
  avatar_url: string | null;
  // True when the backend couldn't resolve a real portal_user row
  // (pre-034 admin-created / seed projects). The UI renders these
  // with a monospace "System" label instead of an email.
  system?: boolean;
}

export interface ProjectDetail {
  project_id: string;
  tenant_id: string;
  tenant_name: string | null;
  name: string;
  policy_profile_id: string | null;
  created_at: string | null;
  archived_at: string | null;
  created_by: Creator | null;
  members: ProjectMemberRow[];
  api_keys_count: number;
  request_count_last_7d: number;
  last_activity_at: string | null;
}

interface Props {
  open: boolean;
  projectId: string | null;
  onClose: () => void;
  onMutated?: () => void;
}

const PALETTE = [
  ["#6366F1", "#8B5CF6"],
  ["#06B6D4", "#6366F1"],
  ["#F59E0B", "#F97316"],
  ["#10B981", "#06B6D4"],
  ["#EC4899", "#8B5CF6"],
  ["#0EA5E9", "#6366F1"],
  ["#F43F5E", "#F59E0B"],
  ["#8B5CF6", "#EC4899"],
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0;
  }
  return h;
}

function Avatar({
  name,
  email,
  id,
  url,
  size = 32,
}: {
  name?: string | null;
  email?: string | null;
  id: string;
  url?: string | null;
  size?: number;
}) {
  const [from, to] = PALETTE[hashString(id) % PALETTE.length];
  const initial = (name || email || "?").slice(0, 1).toUpperCase();
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        overflow: "hidden",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: url
          ? "transparent"
          : `linear-gradient(135deg, ${from}, ${to})`,
        color: "#FFFFFF",
        fontWeight: 600,
        fontSize: Math.max(10, Math.round(size * 0.42)),
        flexShrink: 0,
      }}
    >
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={name || email || ""}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <span>{initial}</span>
      )}
    </div>
  );
}

function formatRelative(iso: string | null, t: Translate): string {
  if (!iso) return t("tenancy.detail.never");
  const d = new Date(iso);
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return t("time.justNow");
  if (sec < 3600) return t("time.minutesAgo", { n: Math.floor(sec / 60) });
  if (sec < 86400) return t("time.hoursAgo", { n: Math.floor(sec / 3600) });
  if (sec < 86400 * 30) return t("time.daysAgo", { n: Math.floor(sec / 86400) });
  return d.toLocaleDateString();
}

export default function ProjectDetailModal({
  open,
  projectId,
  onClose,
  onMutated,
}: Props) {
  const { toast } = useToast();
  const t = useT();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);

  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<ProjectMemberRow | null>(
    null
  );
  const [removing, setRemoving] = useState(false);

  // policy options — kept inline; /admin/policy-profiles would be a
  // nicer source of truth but the dev seed only has "default"/"strict".
  const policyOptions = useMemo(
    () => ["default", "strict"],
    []
  );
  const [editingPolicy, setEditingPolicy] = useState(false);

  const fetchDetail = async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const d = (await api.get(
        `/admin/projects/${projectId}`
      )) as ProjectDetail;
      setDetail(d);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message ||
            t("tenancy.detail.loadFailedStatus", { status: err.status })
          : t("state.errorLoad");
      toast(msg, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open && projectId) fetchDetail();
    else setDetail(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId]);

  async function handleArchive() {
    if (!projectId) return;
    setArchiving(true);
    try {
      await api.post(`/admin/projects/${projectId}/archive`, {});
      toast(t("tenancy.detail.archivedToast"));
      setArchiveOpen(false);
      onMutated?.();
      onClose();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("tenancy.detail.archiveFailed")
          : t("tenancy.detail.archiveFailed");
      toast(msg, "error");
    } finally {
      setArchiving(false);
    }
  }

  async function handleRemoveMember() {
    if (!projectId || !removeTarget) return;
    setRemoving(true);
    try {
      await api.delete(
        `/admin/projects/${projectId}/members/${removeTarget.user_id}`
      );
      toast(t("tenancy.detail.memberRemoved"));
      setRemoveTarget(null);
      await fetchDetail();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("tenancy.detail.removeFailed")
          : t("tenancy.detail.removeFailed");
      toast(msg, "error");
    } finally {
      setRemoving(false);
    }
  }

  async function handleChangeRole(
    userId: string,
    role: "admin" | "member" | "viewer"
  ) {
    if (!projectId) return;
    try {
      await api.patch(
        `/admin/projects/${projectId}/members/${userId}`,
        { role }
      );
      toast(t("tenancy.detail.roleChanged", { role: t(ROLE_LABEL[role]) }));
      await fetchDetail();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("tenancy.detail.roleChangeFailed")
          : t("tenancy.detail.roleChangeFailed");
      toast(msg, "error");
    }
  }

  async function handleSavePolicy(profileId: string) {
    if (!projectId) return;
    try {
      await api.put(`/admin/projects/${projectId}`, {
        policy_profile_id: profileId,
      });
      toast(t("tenancy.detail.policyUpdated"));
      setEditingPolicy(false);
      await fetchDetail();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("tenancy.detail.policyUpdateFailed")
          : t("tenancy.detail.policyUpdateFailed");
      toast(msg, "error");
    }
  }

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title={detail?.name || t("field.project")}
        maxWidth="640px"
        footer={
          <button className="sran-btn sran-btn-secondary" onClick={onClose}>
            {t("action.close")}
          </button>
        }
      >
        {loading && !detail ? (
          <div
            style={{
              padding: "2rem 0",
              textAlign: "center",
              color: "var(--color-text-muted)",
              fontSize: 13,
            }}
          >
            {t("tenancy.detail.loading")}
          </div>
        ) : detail ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <code
              style={{
                fontSize: 11,
                color: "var(--color-text-muted)",
                fontFamily: "var(--ff-mono, 'JetBrains Mono', monospace)",
              }}
            >
              {detail.project_id}
            </code>

            {/* Overview */}
            <Section title={t("tenancy.detail.overview")}>
              <DlRow label={t("field.tenant")} value={detail.tenant_name || detail.tenant_id} />
              <DlRow
                label={t("tenancy.field.policy")}
                value={
                  editingPolicy ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <select
                        className="sran-input"
                        style={{ height: 32, padding: "0 8px", fontSize: 13 }}
                        value={detail.policy_profile_id || ""}
                        onChange={(e) => handleSavePolicy(e.target.value)}
                      >
                        <option value="">—</option>
                        {policyOptions.map((p) => (
                          <option key={p} value={p}>
                            {p}
                          </option>
                        ))}
                      </select>
                      <button
                        className="sran-btn sran-btn-secondary sran-btn-sm"
                        onClick={() => setEditingPolicy(false)}
                      >
                        {t("action.cancel")}
                      </button>
                    </div>
                  ) : (
                    <span>
                      {detail.policy_profile_id || "—"}{" "}
                      <button
                        onClick={() => setEditingPolicy(true)}
                        style={{
                          background: "transparent",
                          border: "none",
                          color: "var(--color-accent-500)",
                          cursor: "pointer",
                          fontSize: 12,
                          fontWeight: 500,
                        }}
                      >
                        {t("tenancy.detail.change")}
                      </button>
                    </span>
                  )
                }
              />
              <DlRow
                label={t("field.created")}
                value={
                  detail.created_at
                    ? new Date(detail.created_at).toLocaleString()
                    : "—"
                }
              />
              <DlRow
                label={t("tenancy.detail.createdBy")}
                value={
                  detail.created_by ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Avatar
                        id={detail.created_by.user_id}
                        name={detail.created_by.display_name}
                        email={detail.created_by.email || undefined}
                        url={detail.created_by.avatar_url}
                        size={28}
                      />
                      <div style={{ fontSize: 13 }}>
                        <div
                          style={{
                            fontWeight: 500,
                            fontFamily: detail.created_by.system
                              ? "var(--ff-mono, 'JetBrains Mono', monospace)"
                              : undefined,
                            color: detail.created_by.system
                              ? "var(--color-text-muted)"
                              : "var(--color-text-primary)",
                          }}
                        >
                          {detail.created_by.display_name || detail.created_by.email || "—"}
                        </div>
                        {detail.created_by.email ? (
                          <div
                            style={{
                              fontSize: 11,
                              color: "var(--color-text-muted)",
                            }}
                          >
                            {detail.created_by.email}
                          </div>
                        ) : detail.created_by.system ? (
                          <div
                            style={{
                              fontSize: 11,
                              color: "var(--color-text-muted)",
                            }}
                          >
                            {t("tenancy.detail.systemCreator")}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ) : (
                    <span style={{ color: "var(--color-text-muted)" }}>—</span>
                  )
                }
              />
              <DlRow
                label={t("tenancy.detail.lastActivity")}
                value={formatRelative(detail.last_activity_at, t)}
              />
              {detail.archived_at ? (
                <DlRow
                  label={t("tenancy.status.archived")}
                  value={
                    <span style={{ color: "var(--color-status-warning)" }}>
                      {new Date(detail.archived_at).toLocaleString()}
                    </span>
                  }
                />
              ) : null}
            </Section>

            {/* Activity */}
            <Section title={t("tenancy.detail.activity")}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 12,
                }}
              >
                <KpiBox
                  value={detail.request_count_last_7d.toLocaleString()}
                  label={t("tenancy.detail.kpiRequests")}
                />
                <KpiBox
                  value={detail.api_keys_count.toString()}
                  label={t(
                    detail.api_keys_count === 1
                      ? "tenancy.detail.kpiApiKey"
                      : "tenancy.detail.kpiApiKeys",
                  )}
                />
                <KpiBox
                  value={detail.members.length.toString()}
                  label={t(
                    detail.members.length === 1
                      ? "tenancy.detail.kpiMember"
                      : "tenancy.detail.kpiMembers",
                  )}
                />
              </div>
            </Section>

            {/* Members */}
            <Section
              title={t("tenancy.detail.members", { n: detail.members.length })}
              actions={
                <button
                  className="sran-btn sran-btn-secondary sran-btn-sm"
                  onClick={() => setAddOpen(true)}
                >
                  {t("tenancy.detail.addMember")}
                </button>
              }
            >
              {detail.members.length === 0 ? (
                <div
                  style={{
                    padding: "16px 0",
                    color: "var(--color-text-muted)",
                    fontSize: 13,
                    textAlign: "center",
                  }}
                >
                  {t("tenancy.detail.noMembers")}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {detail.members.map((m) => (
                    <MemberRow
                      key={m.user_id}
                      member={m}
                      t={t}
                      onChangeRole={(r) => handleChangeRole(m.user_id, r)}
                      onRemove={() => setRemoveTarget(m)}
                    />
                  ))}
                </div>
              )}
            </Section>

            {/* Danger zone */}
            {!detail.archived_at ? (
              <Section title={t("tenancy.detail.dangerZone")} danger>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 16,
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500, fontSize: 13, marginBottom: 2 }}>
                      {t("tenancy.detail.archiveProject")}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {t("tenancy.detail.archiveHint")}
                    </div>
                  </div>
                  <button
                    className="sran-btn sran-btn-secondary"
                    style={{
                      borderColor: "rgba(239,68,68,0.3)",
                      color: "var(--color-status-error)",
                    }}
                    onClick={() => setArchiveOpen(true)}
                  >
                    {t("tenancy.action.archive")}
                  </button>
                </div>
              </Section>
            ) : null}
          </div>
        ) : null}
      </Modal>

      <ConfirmModal
        open={archiveOpen}
        onClose={() => setArchiveOpen(false)}
        onConfirm={handleArchive}
        title={t("tenancy.detail.archiveConfirmTitle")}
        message={t("tenancy.detail.archiveConfirmMessage")}
        confirmLabel={t("tenancy.action.archive")}
        loading={archiving}
      />
      <ConfirmModal
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={handleRemoveMember}
        title={t("tenancy.detail.removeConfirmTitle", { email: removeTarget?.email ?? "" })}
        message={t("tenancy.detail.removeConfirmMessage")}
        confirmLabel={t("action.remove")}
        loading={removing}
      />
      <AddMemberModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onAdded={async () => {
          setAddOpen(false);
          await fetchDetail();
        }}
        projectId={projectId}
        tenantId={detail?.tenant_id || null}
        existingMemberIds={new Set(
          (detail?.members || []).map((m) => m.user_id)
        )}
      />
    </>
  );
}

function Section({
  title,
  children,
  actions,
  danger,
}: {
  title: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  danger?: boolean;
}) {
  return (
    <div
      // Drop the .sran-card class — it carries a light-theme default
      // padding + shadow that clashed with the dark modal body. We
      // reset padding+background inline so both themes resolve via
      // the theme tokens, matching /requests and /audit-log.
      style={{
        padding: 16,
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-elevated)",
        border: danger
          ? "1px solid rgba(239,68,68,0.25)"
          : "1px solid var(--color-border-default)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <h3
          style={{
            margin: 0,
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color: danger
              ? "var(--color-status-error)"
              : "var(--color-text-muted)",
          }}
        >
          {title}
        </h3>
        {actions}
      </div>
      {children}
    </div>
  );
}

function DlRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 16,
        padding: "6px 0",
        borderBottom: "1px solid var(--color-border-default)",
      }}
    >
      <div
        style={{
          width: 120,
          flexShrink: 0,
          fontSize: 11,
          fontWeight: 500,
          color: "var(--color-text-muted)",
          paddingTop: 2,
        }}
      >
        {label}
      </div>
      <div style={{ flex: 1, minWidth: 0, fontSize: 13 }}>{value}</div>
    </div>
  );
}

function KpiBox({ value, label }: { value: string; label: string }) {
  return (
    <div
      style={{
        padding: "10px 14px",
        borderRadius: 8,
        background: "var(--bg-surface)",
        border: "1px solid var(--color-border-default)",
      }}
    >
      <div
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: "var(--color-text-primary)",
          lineHeight: 1.15,
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--color-text-muted)",
          marginTop: 2,
        }}
      >
        {label}
      </div>
    </div>
  );
}

function MemberRow({
  member,
  onChangeRole,
  onRemove,
  t,
}: {
  member: ProjectMemberRow;
  onChangeRole: (r: "admin" | "member" | "viewer") => void;
  onRemove: () => void;
  t: Translate;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 10px",
        borderRadius: 8,
        background: "var(--bg-surface)",
        border: "1px solid var(--color-border-default)",
      }}
    >
      <Avatar
        id={member.user_id}
        name={member.display_name}
        email={member.email}
        url={member.avatar_url}
        size={32}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: "var(--color-text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {member.display_name || member.email}
        </div>
        <div
          style={{
            fontSize: 11.5,
            color: "var(--color-text-muted)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {member.email}
        </div>
      </div>
      <RolePill role={member.role} />
      <div ref={ref} style={{ position: "relative" }}>
        <button
          type="button"
          aria-label={t("tenancy.detail.memberActions")}
          aria-expanded={open}
          aria-haspopup="menu"
          onClick={() => setOpen((v) => !v)}
          style={{
            width: 28,
            height: 28,
            borderRadius: 6,
            border: "1px solid var(--color-border-default)",
            background: "transparent",
            color: "var(--color-text-muted)",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          ⋯
        </button>
        {open ? (
          <div
            role="menu"
            style={{
              position: "absolute",
              right: 0,
              top: "calc(100% + 4px)",
              zIndex: 5,
              background: "var(--bg-surface)",
              border: "1px solid var(--color-border-default)",
              borderRadius: 8,
              boxShadow: "0 14px 32px -10px rgba(10,18,48,0.4)",
              minWidth: 180,
              padding: 5,
            }}
          >
            <div
              style={{
                padding: "4px 10px",
                fontSize: 10.5,
                textTransform: "uppercase",
                letterSpacing: "0.1em",
                color: "var(--color-text-muted)",
              }}
            >
              {t("tenancy.detail.changeRole")}
            </div>
            {(["admin", "member", "viewer"] as const).map((r) => (
              <button
                key={r}
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  if (r !== member.role) onChangeRole(r);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  width: "100%",
                  textAlign: "left",
                  padding: "6px 10px",
                  borderRadius: 5,
                  border: "none",
                  background:
                    r === member.role ? "rgba(99,102,241,0.1)" : "transparent",
                  color: "var(--color-text-primary)",
                  fontSize: 12.5,
                  textTransform: "capitalize",
                  cursor: "pointer",
                }}
              >
                {t(ROLE_LABEL[r])}
                {r === member.role ? <Icon name="check" size={14} /> : null}
              </button>
            ))}
            <div
              style={{
                height: 1,
                background: "var(--color-border-default)",
                margin: "5px 2px",
              }}
            />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onRemove();
              }}
              style={{
                width: "100%",
                textAlign: "left",
                padding: "6px 10px",
                borderRadius: 5,
                border: "none",
                background: "transparent",
                color: "var(--color-status-error)",
                fontSize: 12.5,
                cursor: "pointer",
              }}
            >
              {t("tenancy.detail.removeFromProject")}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RolePill({ role }: { role: "admin" | "member" | "viewer" }) {
  const t = useT();
  const colour =
    role === "admin"
      ? "var(--color-accent-500)"
      : role === "viewer"
      ? "var(--color-text-muted)"
      : "var(--color-text-secondary)";
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 500,
        padding: "2px 8px",
        borderRadius: 999,
        border: `1px solid ${colour}`,
        color: colour,
        background: "transparent",
        textTransform: "capitalize",
      }}
    >
      {ROLE_LABEL[role] ? t(ROLE_LABEL[role]) : role}
    </span>
  );
}

interface SearchableUser {
  id: string;
  display_name: string | null;
  email: string;
  avatar_url: string | null;
}

function AddMemberModal({
  open,
  onClose,
  onAdded,
  projectId,
  tenantId,
  existingMemberIds,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
  projectId: string | null;
  tenantId: string | null;
  existingMemberIds: Set<string>;
}) {
  const { toast } = useToast();
  const t = useT();
  const [search, setSearch] = useState("");
  const [users, setUsers] = useState<SearchableUser[]>([]);
  const [role, setRole] = useState<"admin" | "member" | "viewer">("member");
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<SearchableUser | null>(null);

  useEffect(() => {
    if (!open) {
      setSearch("");
      setPicked(null);
      return;
    }
  }, [open]);

  useEffect(() => {
    if (!open || !tenantId) return;
    let cancelled = false;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const qs = new URLSearchParams({
          tenant_id: tenantId,
          limit: "20",
        });
        if (search.trim()) qs.append("search", search.trim());
        const r = (await api.get(
          `/admin/portal-users?${qs.toString()}`
        )) as { users: SearchableUser[] };
        if (!cancelled) setUsers(r.users || []);
      } catch {
        if (!cancelled) setUsers([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [open, tenantId, search]);

  async function handleAdd() {
    if (!projectId || !picked) return;
    setAdding(true);
    try {
      await api.post(`/admin/projects/${projectId}/members`, {
        user_id: picked.id,
        role,
      });
      toast(t("tenancy.detail.memberAdded"));
      onAdded();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.body?.error?.message || t("tenancy.detail.addFailed")
          : t("tenancy.detail.addFailed");
      toast(msg, "error");
    } finally {
      setAdding(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("tenancy.detail.addMemberTitle")}
      footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={onClose}>
            {t("action.cancel")}
          </button>
          <button
            className="sran-btn sran-btn-primary"
            disabled={!picked || adding}
            onClick={handleAdd}
          >
            {adding ? t("tenancy.detail.adding") : t("action.add")}
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <input
          className="sran-input"
          placeholder={t("tenancy.detail.searchUserPlaceholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />

        <div
          style={{
            maxHeight: 260,
            overflowY: "auto",
            border: "1px solid var(--color-border-default)",
            borderRadius: 8,
            background: "var(--bg-surface)",
          }}
        >
          {loading ? (
            <div
              style={{
                padding: 16,
                textAlign: "center",
                color: "var(--color-text-muted)",
                fontSize: 13,
              }}
            >
              {t("tenancy.detail.searching")}
            </div>
          ) : users.length === 0 ? (
            <div
              style={{
                padding: 16,
                textAlign: "center",
                color: "var(--color-text-muted)",
                fontSize: 13,
              }}
            >
              {t("tenancy.detail.noUsers")}
            </div>
          ) : (
            users.map((u) => {
              const already = existingMemberIds.has(u.id);
              const selected = picked?.id === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  disabled={already}
                  onClick={() => setPicked(u)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    width: "100%",
                    padding: "10px 12px",
                    border: "none",
                    borderBottom: "1px solid var(--color-border-default)",
                    background: selected
                      ? "rgba(99,102,241,0.12)"
                      : "transparent",
                    color: "var(--color-text-primary)",
                    textAlign: "left",
                    cursor: already ? "not-allowed" : "pointer",
                    opacity: already ? 0.55 : 1,
                  }}
                >
                  <Avatar
                    id={u.id}
                    name={u.display_name}
                    email={u.email}
                    url={u.avatar_url}
                    size={28}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                      }}
                    >
                      {u.display_name || u.email}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {u.email}
                    </div>
                  </div>
                  {already ? (
                    <span
                      style={{
                        fontSize: 10.5,
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {t("tenancy.detail.alreadyMember")}
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>

        <div>
          <label
            className="sran-label"
            style={{
              display: "block",
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--color-text-muted)",
              marginBottom: 4,
            }}
          >
            {t("field.role")}
          </label>
          <select
            className="sran-input"
            value={role}
            onChange={(e) =>
              setRole(e.target.value as "admin" | "member" | "viewer")
            }
          >
            <option value="admin">{t("tenancy.role.admin")}</option>
            <option value="member">{t("tenancy.role.member")}</option>
            <option value="viewer">{t("tenancy.role.viewer")}</option>
          </select>
        </div>
      </div>
    </Modal>
  );
}
