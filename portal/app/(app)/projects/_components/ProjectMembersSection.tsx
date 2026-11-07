"use client";

/**
 * Portal-side project-members surface (2026-04-24).
 *
 * Mirrors the admin ProjectDetailModal's Members section, but native
 * to the portal's project-detail page layout and styled with the
 * portal's theme tokens + Team-page patterns.
 *
 * Visibility & gates:
 *   - section is rendered for every role (viewer can see the roster)
 *   - "+ Add member" button only shown when canManage is true (caller
 *     passes true for project-admin or tenant-owner; the backend
 *     enforces independently)
 *   - kebab menu: admin-only options; current-user row always shows
 *     "Leave project" (unless last admin — we surface the server's
 *     409 as a toast rather than pre-disabling, because the client
 *     can't know the admin count authoritatively).
 */

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  DoorOpen,
  Eye,
  Hash,
  MoreVertical,
  Search,
  Shield,
  ShieldCheck,
  UserMinus,
  UserPlus,
  UserRound,
} from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, relativeTime } from "@/lib/format";
import type { TenantMember } from "@/lib/types";

type ProjectRole = "admin" | "member" | "viewer";

export interface ProjectMemberRow {
  user_id: string;
  display_name: string | null;
  email: string;
  avatar_url: string | null;
  role: ProjectRole;
  added_at: string | null;
}

interface Props {
  projectId: string;
  currentUserId: string;
  // canManage = project admin OR tenant owner. Drives the Add button
  // + kebab visibility. The backend enforces separately, so a craftily
  // forged request still fails.
  canManage: boolean;
  // Tenant roster, used to populate the Add-Member search so the
  // picker is scoped to tenant-mates not already on this project.
  tenantMembers: TenantMember[];
}

interface MembersResponse {
  members: ProjectMemberRow[];
}

export function ProjectMembersSection({
  projectId,
  currentUserId,
  canManage,
  tenantMembers,
}: Props) {
  const { toast } = useToast();
  const [members, setMembers] = useState<ProjectMemberRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<ProjectMemberRow | null>(
    null
  );
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [pending, setPending] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<MembersResponse>(
        `/portal/projects/${projectId}/members`
      );
      setMembers(r.members);
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to load members."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleRemove() {
    if (!removeTarget) return;
    setPending(true);
    try {
      await api.delete(
        `/portal/projects/${projectId}/members/${removeTarget.user_id}`
      );
      toast(`${removeTarget.email} removed from project`, "success");
      setRemoveTarget(null);
      await load();
    } catch (err) {
      toast(
        err instanceof PortalApiError ? err.message : "Remove failed",
        "error"
      );
    } finally {
      setPending(false);
    }
  }

  async function handleLeave() {
    setPending(true);
    try {
      await api.delete(
        `/portal/projects/${projectId}/members/${currentUserId}`
      );
      toast("You left the project", "success");
      // Route back to list: the user no longer has access to this page.
      window.location.href = "/projects";
    } catch (err) {
      toast(
        err instanceof PortalApiError ? err.message : "Leave failed",
        "error"
      );
      setPending(false);
      setLeaveOpen(false);
    }
  }

  async function handleRoleChange(userId: string, role: ProjectRole) {
    try {
      await api.patch(
        `/portal/projects/${projectId}/members/${userId}`,
        { role }
      );
      toast(`Role changed to ${role}`, "success");
      await load();
    } catch (err) {
      toast(
        err instanceof PortalApiError ? err.message : "Role change failed",
        "error"
      );
    }
  }

  const membersSorted = useMemo(() => {
    if (!members) return [];
    const priority: Record<ProjectRole, number> = {
      admin: 0,
      member: 1,
      viewer: 2,
    };
    return [...members].sort((a, b) => {
      const pa = priority[a.role] ?? 3;
      const pb = priority[b.role] ?? 3;
      if (pa !== pb) return pa - pb;
      return (a.display_name || a.email).localeCompare(
        b.display_name || b.email
      );
    });
  }, [members]);

  const addableUsers = useMemo(() => {
    // Exclude tenant-mates who are already on this project.
    const existing = new Set((members || []).map((m) => m.user_id));
    return tenantMembers.filter((u) => !existing.has(u.id));
  }, [tenantMembers, members]);

  return (
    <>
      <Card padding="md">
        <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
          <h2
            className="font-heading"
            style={{
              fontSize: 16,
              fontWeight: 600,
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            Members ({members?.length ?? 0})
          </h2>
          {canManage ? (
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <UserPlus size={14} className="mr-1" strokeWidth={2} />
              Add member
            </Button>
          ) : null}
        </div>

        {loading ? (
          <div
            style={{
              padding: "24px 0",
              color: "var(--text-tertiary)",
              fontSize: 13,
              textAlign: "center",
            }}
          >
            Loading members…
          </div>
        ) : error ? (
          <div style={{ color: "var(--accent-danger)", fontSize: 13 }}>
            {error}
          </div>
        ) : membersSorted.length === 0 ? (
          <div
            style={{
              padding: "20px 0",
              color: "var(--text-tertiary)",
              fontSize: 13,
              textAlign: "center",
            }}
          >
            No members yet. Add teammates to grant them access to this project.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {membersSorted.map((m) => (
              <MemberRow
                key={m.user_id}
                member={m}
                currentUserId={currentUserId}
                canManage={canManage}
                onRemove={() => setRemoveTarget(m)}
                onLeave={() => setLeaveOpen(true)}
                onRoleChange={(role) => handleRoleChange(m.user_id, role)}
              />
            ))}
          </div>
        )}
      </Card>

      <AddMemberModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onAdded={async () => {
          setAddOpen(false);
          await load();
        }}
        projectId={projectId}
        options={addableUsers}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={handleRemove}
        title={`Remove ${removeTarget?.email ?? ""}?`}
        confirmLabel="Remove member"
        loading={pending}
        message={
          <>
            They&apos;ll lose access to this project immediately. Their
            tenant membership is unaffected.
          </>
        }
      />

      <ConfirmDialog
        open={leaveOpen}
        onClose={() => setLeaveOpen(false)}
        onConfirm={handleLeave}
        title="Leave this project?"
        confirmLabel="Leave project"
        loading={pending}
        message={
          <>
            You&apos;ll no longer see this project in your sidebar. An admin
            can add you back later.
          </>
        }
      />
    </>
  );
}

function MemberRow({
  member,
  currentUserId,
  canManage,
  onRemove,
  onLeave,
  onRoleChange,
}: {
  member: ProjectMemberRow;
  currentUserId: string;
  canManage: boolean;
  onRemove: () => void;
  onLeave: () => void;
  onRoleChange: (r: ProjectRole) => void;
}) {
  const self = member.user_id === currentUserId;
  const showKebab = canManage || self;

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
      <Avatar
        url={member.avatar_url}
        name={member.display_name}
        email={member.email}
        id={member.user_id}
        size={34}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-primary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: 260,
          }}
        >
          {member.display_name || member.email}
          {self ? (
            <span
              style={{
                marginLeft: 6,
                padding: "1px 6px",
                borderRadius: 999,
                fontSize: 10.5,
                fontWeight: 600,
                background: "rgba(99,102,241,0.12)",
                color: "var(--accent-text)",
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
      <RoleBadge role={member.role} />
      {showKebab ? (
        <RowMenu
          self={self}
          canManage={canManage}
          role={member.role}
          onRoleChange={onRoleChange}
          onRemove={onRemove}
          onLeave={onLeave}
        />
      ) : (
        <div style={{ width: 30 }} />
      )}
    </div>
  );
}

function RoleBadge({ role }: { role: ProjectRole }) {
  const meta =
    role === "admin"
      ? {
          bg: "rgba(245,158,11,0.12)",
          color: "var(--accent-warning)",
          border: "rgba(245,158,11,0.3)",
          Icon: ShieldCheck,
          label: "Admin",
        }
      : role === "member"
      ? {
          bg: "var(--bg-elevated)",
          color: "var(--text-secondary)",
          border: "var(--border-default)",
          Icon: UserRound,
          label: "Member",
        }
      : {
          bg: "var(--bg-elevated)",
          color: "var(--text-tertiary)",
          border: "var(--border-default)",
          Icon: Eye,
          label: "Viewer",
        };
  const Icon = meta.Icon;
  return (
    <span
      className="inline-flex items-center gap-1.5"
      style={{
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 11.5,
        fontWeight: 500,
        background: meta.bg,
        color: meta.color,
        border: `1px solid ${meta.border}`,
        flexShrink: 0,
      }}
    >
      <Icon size={11} />
      {meta.label}
    </span>
  );
}

function RowMenu({
  self,
  canManage,
  role,
  onRoleChange,
  onRemove,
  onLeave,
}: {
  self: boolean;
  canManage: boolean;
  role: ProjectRole;
  onRoleChange: (r: ProjectRole) => void;
  onRemove: () => void;
  onLeave: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [direction, setDirection] = useState<"down" | "up">("down");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  function toggle() {
    if (open) {
      beginClose();
      return;
    }
    const btn = triggerRef.current;
    if (btn) {
      const rect = btn.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      setDirection(spaceBelow < 180 ? "up" : "down");
    }
    setClosing(false);
    setOpen(true);
  }

  function beginClose() {
    if (!open || closing) return;
    setClosing(true);
    window.setTimeout(() => {
      setOpen(false);
      setClosing(false);
      triggerRef.current?.focus();
    }, 150);
  }

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) beginClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        beginClose();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.setTimeout(() => {
      menuRef.current
        ?.querySelector<HTMLElement>("[role='menuitem']:not([disabled])")
        ?.focus();
    }, 0);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const animClass = closing
    ? direction === "up"
      ? "menu-anim-out-up"
      : "menu-anim-out-down"
    : direction === "up"
    ? "menu-anim-in-up"
    : "menu-anim-in-down";

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        ref={triggerRef}
        type="button"
        aria-label="Member actions"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={toggle}
        style={{
          width: 30,
          height: 30,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          background: "transparent",
          border: "1px solid var(--border-default)",
          borderRadius: 8,
          color: "var(--text-secondary)",
          cursor: "pointer",
        }}
      >
        <MoreVertical size={15} />
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          className={animClass}
          style={{
            position: "absolute",
            right: 0,
            top: direction === "down" ? "calc(100% + 4px)" : "auto",
            bottom: direction === "up" ? "calc(100% + 4px)" : "auto",
            zIndex: 20,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-default)",
            borderRadius: 10,
            boxShadow:
              "0 14px 36px -14px rgba(10,18,48,0.35), 0 4px 10px rgba(10,18,48,0.1)",
            minWidth: 200,
            padding: 6,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          {canManage
            ? (["admin", "member", "viewer"] as ProjectRole[]).map((r) => (
                <MenuItem
                  key={r}
                  icon={
                    r === "admin" ? (
                      <ShieldCheck size={13} />
                    ) : r === "member" ? (
                      <UserRound size={13} />
                    ) : (
                      <Eye size={13} />
                    )
                  }
                  label={`Make ${r}`}
                  active={role === r}
                  disabled={role === r}
                  onClick={() => {
                    beginClose();
                    onRoleChange(r);
                  }}
                />
              ))
            : null}
          {canManage ? (
            <div
              style={{
                height: 1,
                background: "var(--border-subtle)",
                margin: "4px 2px",
              }}
            />
          ) : null}
          {self ? (
            <MenuItem
              icon={<DoorOpen size={13} />}
              label="Leave project"
              tone="danger"
              onClick={() => {
                beginClose();
                onLeave();
              }}
            />
          ) : canManage ? (
            <MenuItem
              icon={<UserMinus size={13} />}
              label="Remove from project"
              tone="danger"
              onClick={() => {
                beginClose();
                onRemove();
              }}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  tone,
  active,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  tone?: "danger";
  active?: boolean;
  disabled?: boolean;
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
        background: active ? "rgba(99,102,241,0.1)" : "transparent",
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
        if (!disabled && !active)
          e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = "transparent";
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            color: tone === "danger" ? color : "var(--text-tertiary)",
          }}
        >
          {icon}
        </span>
        {label}
      </span>
      {active ? <Check size={12} /> : null}
    </button>
  );
}

function AddMemberModal({
  open,
  onClose,
  onAdded,
  projectId,
  options,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
  projectId: string;
  options: TenantMember[];
}) {
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<TenantMember | null>(null);
  const [role, setRole] = useState<ProjectRole>("member");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    if (!open) {
      setSearch("");
      setPicked(null);
      setRole("member");
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (u) =>
        u.email.toLowerCase().includes(q) ||
        (u.display_name || "").toLowerCase().includes(q)
    );
  }, [options, search]);

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!picked) return;
    setAdding(true);
    try {
      await api.post(
        `/portal/projects/${projectId}/members`,
        { user_id: picked.id, role }
      );
      toast(`${picked.email} added as ${role}`, "success");
      onAdded();
    } catch (err) {
      toast(
        err instanceof PortalApiError ? err.message : "Add failed",
        "error"
      );
    } finally {
      setAdding(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add member to project"
      widthClass="max-w-lg"
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!picked || adding}
            loading={adding}
            onClick={handleAdd as unknown as () => void}
          >
            Add member
          </Button>
        </>
      }
    >
      <form onSubmit={handleAdd} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <label
            style={{
              display: "block",
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-tertiary)",
              marginBottom: 6,
            }}
          >
            Search tenant mates
          </label>
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
              placeholder="Name or email"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
              style={{
                width: "100%",
                height: 38,
                padding: "0 12px 0 30px",
                borderRadius: 8,
                border: "1px solid var(--border-default)",
                background: "var(--bg-surface)",
                color: "var(--text-primary)",
                fontSize: 13,
                outline: "none",
              }}
            />
          </div>
        </div>

        <div
          style={{
            maxHeight: 220,
            overflowY: "auto",
            border: "1px solid var(--border-default)",
            borderRadius: 8,
            background: "var(--bg-surface)",
          }}
        >
          {filtered.length === 0 ? (
            <div
              style={{
                padding: 16,
                textAlign: "center",
                color: "var(--text-tertiary)",
                fontSize: 13,
              }}
            >
              {options.length === 0
                ? "Everyone in your tenant is already on this project."
                : "No tenant mates match that search."}
            </div>
          ) : (
            filtered.map((u) => {
              const selected = picked?.id === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  onClick={() => setPicked(u)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    width: "100%",
                    padding: "8px 12px",
                    border: "none",
                    borderBottom: "1px solid var(--border-subtle)",
                    background: selected
                      ? "rgba(99,102,241,0.12)"
                      : "transparent",
                    color: "var(--text-primary)",
                    textAlign: "left",
                    cursor: "pointer",
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
                    <div style={{ fontSize: 13, fontWeight: 500 }}>
                      {u.display_name || u.email}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--text-tertiary)",
                      }}
                    >
                      {u.email}
                    </div>
                  </div>
                  {selected ? <Check size={14} /> : null}
                </button>
              );
            })
          )}
        </div>

        <div>
          <label
            style={{
              display: "block",
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-tertiary)",
              marginBottom: 6,
            }}
          >
            Role
          </label>
          <div
            role="radiogroup"
            aria-label="Role"
            style={{ display: "flex", gap: 8 }}
          >
            {(
              [
                {
                  r: "admin" as const,
                  label: "Admin",
                  hint: "Manage members + keys",
                  Icon: ShieldCheck,
                },
                {
                  r: "member" as const,
                  label: "Member",
                  hint: "Use keys",
                  Icon: UserRound,
                },
                {
                  r: "viewer" as const,
                  label: "Viewer",
                  hint: "Read-only",
                  Icon: Eye,
                },
              ]
            ).map((opt) => {
              const active = role === opt.r;
              const Icon = opt.Icon;
              return (
                <button
                  key={opt.r}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setRole(opt.r)}
                  style={{
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    gap: 2,
                    padding: "10px 12px",
                    borderRadius: 8,
                    background: active ? "rgba(99,102,241,0.1)" : "var(--bg-surface)",
                    border: `1px solid ${active ? "var(--accent-primary)" : "var(--border-default)"}`,
                    color: "var(--text-primary)",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 12.5,
                      fontWeight: 500,
                    }}
                  >
                    <Icon size={12} />
                    {opt.label}
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-tertiary)",
                    }}
                  >
                    {opt.hint}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </form>
    </Modal>
  );
}
