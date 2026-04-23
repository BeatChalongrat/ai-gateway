"use client";

/**
 * Team management page.
 *
 * Role model (phase portal-UX):
 *   - owner   — full control; can invite, remove, transfer ownership.
 *   - member  — read-only view of the roster, can self-leave.
 *
 * Visual layers built on top of the backend role/status model:
 *   - crown badge + "Owner" for tenant_role=owner
 *   - "Active" / "Suspended" / "Pending" status pill per member row
 *   - deterministic avatar circle, greyed row for suspended
 *   - search + status filter across members
 *   - pending-invite section (owner-only), polished card layout
 *   - per-row kebab menu (owner) — change role / remove (stubbed for
 *     role changes; remove is live)
 */

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Copy,
  Crown,
  DoorOpen,
  Mail,
  MoreVertical,
  Search,
  Shield,
  Trash2,
  UserCheck,
  UserMinus,
  UserPlus,
} from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Input } from "@/components/Input";
import { PageSpinner } from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, relativeTime } from "@/lib/format";
import type { PortalUser, TenantInvite, TenantMember } from "@/lib/types";

import PageHeader from "../_components/PageHeader";

interface MembersResponse {
  members: TenantMember[];
}

interface InvitesResponse {
  invites: TenantInvite[];
}

type StatusFilter = "all" | "active" | "suspended" | "pending";

export default function TeamPage() {
  const { toast } = useToast();
  const [me, setMe] = useState<PortalUser | null>(null);
  const [members, setMembers] = useState<TenantMember[] | null>(null);
  const [invites, setInvites] = useState<TenantInvite[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [inviteEmailHint, setInviteEmailHint] = useState("");
  const [creating, setCreating] = useState(false);
  const [lastGenerated, setLastGenerated] = useState<TenantInvite | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const [leaveOpen, setLeaveOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<TenantMember | null>(null);
  const [pendingAction, setPendingAction] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<TenantInvite | null>(null);

  async function refresh() {
    setError(null);
    try {
      const user = await api.get<PortalUser>("/portal/me");
      setMe(user);
      const m = await api.get<MembersResponse>("/portal/team/members");
      setMembers(m.members);
      if (user.tenant_role === "owner") {
        const inv = await api.get<InvitesResponse>("/portal/team/invites");
        setInvites(inv.invites);
      } else {
        setInvites(null);
      }
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to load team data."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleGenerateInvite(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const invite = await api.post<TenantInvite>(
        "/portal/team/invites",
        { email_hint: inviteEmailHint.trim() || undefined }
      );
      setLastGenerated(invite);
      setInviteEmailHint("");
      toast("Invite code generated", "success");
      await refresh();
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to create invite.";
      setError(msg);
      toast(msg, "error");
    } finally {
      setCreating(false);
    }
  }

  async function confirmRevoke() {
    if (!revokeTarget) return;
    setPendingAction(true);
    try {
      await api.delete(`/portal/team/invites/${revokeTarget.id}`);
      toast("Invite revoked", "success");
      setRevokeTarget(null);
      await refresh();
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to revoke invite.";
      toast(msg, "error");
    } finally {
      setPendingAction(false);
    }
  }

  async function confirmLeave() {
    if (!me) return;
    setPendingAction(true);
    try {
      await api.delete(`/portal/team/members/${me.id}`);
      window.location.href = "/login";
    } catch (err) {
      const msg =
        err instanceof PortalApiError
          ? err.message
          : "Failed to leave team.";
      toast(msg, "error");
      setPendingAction(false);
      setLeaveOpen(false);
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return;
    setPendingAction(true);
    try {
      await api.delete(`/portal/team/members/${removeTarget.id}`);
      toast(`${removeTarget.email} removed from team`, "success");
      setRemoveTarget(null);
      await refresh();
    } catch (err) {
      const msg =
        err instanceof PortalApiError
          ? err.message
          : "Failed to remove member.";
      toast(msg, "error");
    } finally {
      setPendingAction(false);
    }
  }

  function copyToClipboard(value: string, key: string, label: string) {
    navigator.clipboard.writeText(value);
    setCopied(key);
    toast(`${label} copied`, "success");
    setTimeout(() => setCopied(null), 2000);
  }

  const filteredMembers = useMemo(() => {
    if (!members) return [];
    const q = search.trim().toLowerCase();
    return members.filter((m) => {
      const status = (m.status || "active").toLowerCase();
      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "active" && status === "active") ||
        (statusFilter === "suspended" && status === "suspended") ||
        (statusFilter === "pending" && status === "pending");
      if (!matchesStatus) return false;
      if (!q) return true;
      return (
        m.email.toLowerCase().includes(q) ||
        (m.display_name || "").toLowerCase().includes(q)
      );
    });
  }, [members, search, statusFilter]);

  const counts = useMemo(() => {
    const c = { all: 0, active: 0, suspended: 0, pending: 0 };
    (members || []).forEach((m) => {
      c.all += 1;
      const s = (m.status || "active").toLowerCase();
      if (s === "suspended") c.suspended += 1;
      else if (s === "pending") c.pending += 1;
      else c.active += 1;
    });
    return c;
  }, [members]);

  if (loading) return <PageSpinner />;
  if (error) {
    return (
      <div className="space-y-6">
        <PageHeader title="Team" subtitle="Manage your team members and invites." />
        <Card>
          <p style={{ color: "var(--accent-danger)", fontSize: 13 }}>{error}</p>
        </Card>
      </div>
    );
  }
  if (!me) return null;

  const isOwner = me.tenant_role === "owner";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team"
        subtitle={
          isOwner
            ? "Invite teammates, manage roles, and revoke access."
            : "Your team members. Only the owner can invite new people."
        }
      />

      {isOwner && (
        <Card padding="md">
          <div className="flex items-center gap-2 mb-4">
            <UserPlus size={16} style={{ color: "var(--accent-primary)" }} />
            <h2
              className="font-heading"
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: "var(--text-primary)",
                margin: 0,
              }}
            >
              Invite a teammate
            </h2>
          </div>
          <form
            onSubmit={handleGenerateInvite}
            className="flex flex-col gap-3 md:flex-row md:items-end"
          >
            <div className="flex-1 min-w-0">
              <Input
                label="Email hint (optional)"
                type="email"
                placeholder="bob@teammate.com"
                value={inviteEmailHint}
                onChange={(e) => setInviteEmailHint(e.target.value)}
              />
              <p
                style={{
                  marginTop: 6,
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                Display-only — the invite code is usable by anyone who has it.
              </p>
            </div>
            <Button type="submit" loading={creating}>
              Generate code
            </Button>
          </form>

          {lastGenerated && (
            <div
              className="mt-4 p-3"
              style={{
                borderRadius: 10,
                background: "rgba(16,185,129,0.08)",
                border: "1px solid rgba(16,185,129,0.25)",
              }}
            >
              <div
                style={{
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: "var(--accent-success)",
                  marginBottom: 4,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Check size={12} /> Invite created
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  marginBottom: 10,
                }}
              >
                Send this code to your teammate via Slack, email, or any other
                channel. Expires {formatDate(lastGenerated.expires_at)}.
              </div>
              <div
                className="flex gap-2 items-center"
                style={{
                  padding: "6px 10px",
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-default)",
                  borderRadius: 8,
                }}
              >
                <code
                  style={{
                    flex: 1,
                    fontSize: 12,
                    fontFamily: "var(--ff-mono)",
                    color: "var(--text-primary)",
                    wordBreak: "break-all",
                  }}
                >
                  {lastGenerated.code}
                </code>
                <button
                  type="button"
                  onClick={() =>
                    copyToClipboard(lastGenerated.code, lastGenerated.code, "Invite code")
                  }
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1"
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: "var(--accent-primary)",
                    background: "transparent",
                    border: "1px solid var(--border-default)",
                    cursor: "pointer",
                  }}
                >
                  {copied === lastGenerated.code ? (
                    <>
                      <Check size={11} /> Copied
                    </>
                  ) : (
                    <>
                      <Copy size={11} /> Copy
                    </>
                  )}
                </button>
              </div>
            </div>
          )}
        </Card>
      )}

      {isOwner && invites && invites.length > 0 && (
        <Card padding="md">
          <div className="flex items-center justify-between mb-3">
            <h2
              className="font-heading"
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: "var(--text-primary)",
                margin: 0,
              }}
            >
              Pending invites
            </h2>
            <span
              style={{
                fontSize: 11,
                color: "var(--text-tertiary)",
                fontWeight: 500,
              }}
            >
              {invites.length} waiting
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {invites.map((inv) => (
              <PendingInviteRow
                key={inv.id}
                invite={inv}
                onCopy={(code) => copyToClipboard(code, inv.id, "Invite code")}
                copiedKey={copied}
                onRevoke={() => setRevokeTarget(inv)}
              />
            ))}
          </div>
        </Card>
      )}

      <Card padding="md">
        <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
          <h2
            className="font-heading"
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            Members ({members?.length ?? 0})
          </h2>
          <div className="flex items-center gap-2 flex-wrap">
            <div style={{ position: "relative" }}>
              <Search
                size={13}
                style={{
                  position: "absolute",
                  left: 10,
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "var(--text-tertiary)",
                  pointerEvents: "none",
                }}
              />
              <input
                type="search"
                placeholder="Search name or email"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  height: 32,
                  padding: "0 10px 0 30px",
                  borderRadius: 8,
                  border: "1px solid var(--border-default)",
                  background: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  fontSize: 12.5,
                  width: 220,
                  outline: "none",
                }}
              />
            </div>
            <FilterChips
              current={statusFilter}
              counts={counts}
              onChange={setStatusFilter}
            />
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              fontSize: 13,
              borderCollapse: "collapse",
            }}
          >
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                <Th>Member</Th>
                <Th>Role</Th>
                <Th>Status</Th>
                <Th>Joined</Th>
                <th style={{ width: 44 }} />
              </tr>
            </thead>
            <tbody>
              {filteredMembers.length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ padding: "28px 0", textAlign: "center" }}>
                    <div style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
                      No members match these filters.
                    </div>
                  </td>
                </tr>
              ) : (
                filteredMembers.map((m) => (
                  <MemberRow
                    key={m.id}
                    member={m}
                    currentUserId={me.id}
                    isOwner={isOwner}
                    onLeave={() => setLeaveOpen(true)}
                    onRemove={() => setRemoveTarget(m)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <ConfirmDialog
        open={leaveOpen}
        onClose={() => setLeaveOpen(false)}
        onConfirm={confirmLeave}
        title="Leave this team?"
        confirmLabel="Leave team"
        loading={pendingAction}
        message={
          <>
            You&apos;ll lose access to your tenant&apos;s projects, API keys,
            and usage history. Existing API keys will stop working. The owner
            can invite you back later.
          </>
        }
      />

      <ConfirmDialog
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={confirmRemove}
        title={`Remove ${removeTarget?.email ?? ""}?`}
        confirmLabel="Remove member"
        loading={pendingAction}
        message={
          <>
            Their existing sessions will end and their API keys will stop
            working immediately. Usage history stays in the audit log.
          </>
        }
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        onConfirm={confirmRevoke}
        title="Revoke this invite?"
        confirmLabel="Revoke invite"
        loading={pendingAction}
        message={
          <>
            The code will stop working immediately. You can always generate a
            fresh one if you still want to invite
            {revokeTarget?.email_hint ? ` ${revokeTarget.email_hint}` : " this person"}.
          </>
        }
      />
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        padding: "10px 16px 10px 0",
        textAlign: "left",
        fontSize: 10.5,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

function MemberRow({
  member,
  currentUserId,
  isOwner,
  onLeave,
  onRemove,
}: {
  member: TenantMember;
  currentUserId: string;
  isOwner: boolean;
  onLeave: () => void;
  onRemove: () => void;
}) {
  const self = member.id === currentUserId;
  const status = (member.status || "active").toLowerCase();
  const suspended = status === "suspended";

  return (
    <tr
      className="row-hover"
      style={{
        borderBottom: "1px solid var(--border-subtle)",
        opacity: suspended ? 0.72 : 1,
      }}
    >
      <td style={{ padding: "10px 16px 10px 0" }}>
        <div className="flex items-center gap-3 min-w-0">
          <Avatar
            url={member.avatar_url}
            name={member.display_name}
            email={member.email}
            id={member.id}
            size={34}
          />
          <div className="min-w-0">
            <div
              style={{
                fontSize: 13,
                fontWeight: 500,
                color: suspended
                  ? "var(--text-tertiary)"
                  : "var(--text-primary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 280,
              }}
            >
              {member.display_name || (
                <em style={{ color: "var(--text-tertiary)" }}>—</em>
              )}
              {self ? (
                <span
                  style={{
                    marginLeft: 6,
                    padding: "1px 6px",
                    borderRadius: 999,
                    fontSize: 10.5,
                    fontWeight: 600,
                    background: "rgba(99,102,241,0.12)",
                    color: "var(--accent-primary)",
                    border: "1px solid rgba(99,102,241,0.25)",
                  }}
                >
                  you
                </span>
              ) : null}
            </div>
            <div
              style={{
                fontSize: 11.5,
                color: "var(--text-tertiary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 320,
              }}
            >
              {member.email}
            </div>
          </div>
        </div>
      </td>
      <td style={{ padding: "10px 16px 10px 0" }}>
        <RoleBadge role={member.tenant_role} />
      </td>
      <td style={{ padding: "10px 16px 10px 0" }}>
        <StatusBadge status={status} />
      </td>
      <td
        style={{
          padding: "10px 16px 10px 0",
          color: "var(--text-secondary)",
          fontSize: 12.5,
          whiteSpace: "nowrap",
        }}
        title={formatDate(member.created_at)}
      >
        {relativeTime(member.created_at)}
      </td>
      <td style={{ padding: "10px 0 10px 0", textAlign: "right" }}>
        {self ? (
          <button
            type="button"
            onClick={onLeave}
            aria-label="Leave team"
            title="Leave team"
            style={iconBtnStyle("danger")}
          >
            <DoorOpen size={15} />
          </button>
        ) : isOwner ? (
          <RowMenu onRemove={onRemove} />
        ) : null}
      </td>
    </tr>
  );
}

function RoleBadge({ role }: { role: "owner" | "member" }) {
  if (role === "owner") {
    return (
      <span
        className="inline-flex items-center gap-1.5"
        style={{
          padding: "2px 8px",
          borderRadius: 999,
          fontSize: 11.5,
          fontWeight: 600,
          background: "rgba(245,158,11,0.12)",
          color: "var(--accent-warning)",
          border: "1px solid rgba(245,158,11,0.3)",
        }}
      >
        <Crown size={11} />
        Owner
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1.5"
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11.5,
        fontWeight: 500,
        background: "var(--bg-elevated)",
        color: "var(--text-secondary)",
        border: "1px solid var(--border-default)",
      }}
    >
      <UserCheck size={11} />
      Member
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "suspended") {
    return (
      <span
        className="inline-flex items-center gap-1"
        style={{
          padding: "2px 8px",
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 500,
          background: "rgba(248,113,113,0.12)",
          color: "var(--accent-danger)",
          border: "1px solid rgba(248,113,113,0.25)",
        }}
      >
        Suspended
      </span>
    );
  }
  if (status === "pending") {
    return (
      <span
        className="inline-flex items-center gap-1"
        style={{
          padding: "2px 8px",
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 500,
          background: "rgba(245,158,11,0.12)",
          color: "var(--accent-warning)",
          border: "1px solid rgba(245,158,11,0.25)",
        }}
      >
        Pending
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1"
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 500,
        background: "rgba(16,185,129,0.12)",
        color: "var(--accent-success)",
        border: "1px solid rgba(16,185,129,0.25)",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: "var(--accent-success)",
          display: "inline-block",
        }}
      />
      Active
    </span>
  );
}

function FilterChips({
  current,
  counts,
  onChange,
}: {
  current: StatusFilter;
  counts: { all: number; active: number; suspended: number; pending: number };
  onChange: (s: StatusFilter) => void;
}) {
  const chips: { key: StatusFilter; label: string; count: number }[] = [
    { key: "all", label: "All", count: counts.all },
    { key: "active", label: "Active", count: counts.active },
    { key: "suspended", label: "Suspended", count: counts.suspended },
    { key: "pending", label: "Pending", count: counts.pending },
  ];
  return (
    <div
      style={{
        display: "inline-flex",
        background: "var(--bg-elevated)",
        borderRadius: 8,
        padding: 3,
        border: "1px solid var(--border-default)",
      }}
    >
      {chips.map((c) => {
        const active = current === c.key;
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => onChange(c.key)}
            style={{
              padding: "5px 10px",
              fontSize: 11.5,
              fontWeight: 500,
              borderRadius: 6,
              background: active ? "var(--bg-surface)" : "transparent",
              color: active ? "var(--text-primary)" : "var(--text-secondary)",
              border: active ? "1px solid var(--border-default)" : "1px solid transparent",
              cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {c.label}
            <span
              style={{
                marginLeft: 6,
                fontSize: 10,
                color: "var(--text-tertiary)",
                fontWeight: 500,
              }}
            >
              {c.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function RowMenu({ onRemove }: { onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        aria-label="Row actions"
        title="Actions"
        onClick={() => setOpen((v) => !v)}
        style={iconBtnStyle("ghost")}
      >
        <MoreVertical size={15} />
      </button>
      {open ? (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            zIndex: 20,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-default)",
            borderRadius: 10,
            boxShadow:
              "0 14px 36px -14px rgba(10,18,48,0.35), 0 4px 10px rgba(10,18,48,0.1)",
            minWidth: 180,
            padding: 6,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <MenuItem
            icon={<Shield size={13} />}
            label="Change role"
            disabled
            hint="Coming soon"
          />
          <div
            style={{
              height: 1,
              background: "var(--border-subtle)",
              margin: "4px 2px",
            }}
          />
          <MenuItem
            icon={<UserMinus size={13} />}
            label="Remove from team"
            tone="danger"
            onClick={() => {
              setOpen(false);
              onRemove();
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  tone,
  disabled,
  hint,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  tone?: "danger";
  disabled?: boolean;
  hint?: string;
  onClick?: () => void;
}) {
  const color =
    tone === "danger" ? "var(--accent-danger)" : "var(--text-primary)";
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "7px 10px",
        borderRadius: 7,
        background: "transparent",
        border: "none",
        color,
        fontSize: 12.5,
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        width: "100%",
        textAlign: "left",
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
      }}
    >
      <span
        style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
      >
        <span style={{ color: tone === "danger" ? color : "var(--text-tertiary)" }}>
          {icon}
        </span>
        {label}
      </span>
      {hint ? (
        <span
          style={{
            fontSize: 10,
            color: "var(--text-tertiary)",
            fontWeight: 500,
          }}
        >
          {hint}
        </span>
      ) : null}
    </button>
  );
}

function PendingInviteRow({
  invite,
  onCopy,
  onRevoke,
  copiedKey,
}: {
  invite: TenantInvite;
  onCopy: (code: string) => void;
  onRevoke: () => void;
  copiedKey: string | null;
}) {
  const expiresAt = new Date(invite.expires_at);
  const daysLeft = Math.max(
    0,
    Math.ceil((expiresAt.getTime() - Date.now()) / (1000 * 60 * 60 * 24))
  );

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 12px",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        borderRadius: 10,
      }}
    >
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: 8,
          background: "rgba(245,158,11,0.12)",
          color: "var(--accent-warning)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Mail size={14} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {invite.email_hint || (
            <em style={{ color: "var(--text-tertiary)" }}>
              unspecified recipient
            </em>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 1 }}>
          Expires in {daysLeft} day{daysLeft === 1 ? "" : "s"} ·{" "}
          {formatDate(invite.expires_at)}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onCopy(invite.code)}
        title={invite.code}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1"
        style={{
          fontSize: 11,
          fontWeight: 500,
          color: "var(--accent-primary)",
          background: "transparent",
          border: "1px solid var(--border-default)",
          cursor: "pointer",
          fontFamily: "var(--ff-mono)",
        }}
      >
        {copiedKey === invite.id ? (
          <>
            <Check size={11} /> Copied
          </>
        ) : (
          <>
            <Copy size={11} /> {invite.code.slice(0, 8)}…
          </>
        )}
      </button>
      <button
        type="button"
        aria-label="Revoke invite"
        title="Revoke invite"
        onClick={onRevoke}
        style={iconBtnStyle("danger")}
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
}

function iconBtnStyle(tone: "danger" | "ghost"): React.CSSProperties {
  const danger = tone === "danger";
  return {
    width: 30,
    height: 30,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    background: "transparent",
    border: "1px solid var(--border-default)",
    borderRadius: 8,
    color: danger ? "var(--accent-danger)" : "var(--text-secondary)",
    cursor: "pointer",
    transition: "background 150ms, color 150ms, border-color 150ms",
  };
}
