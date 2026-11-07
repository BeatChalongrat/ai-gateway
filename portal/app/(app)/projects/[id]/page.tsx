"use client";

import { useParams, useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  Copy,
  FolderOpen,
  Hash,
  Key,
  Plus,
  RotateCw,
  Zap,
} from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Input } from "@/components/Input";
import { Modal } from "@/components/Modal";
import { StatCardRowSkeleton } from "@/components/skeletons/StatCardRowSkeleton";
import { TableSkeleton } from "@/components/skeletons/TableSkeleton";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, formatTokens, relativeTime } from "@/lib/format";
import type {
  ApiKey,
  ApiKeyCreated,
  PortalUser,
  Project,
  TenantMember,
  UsageRequest,
} from "@/lib/types";

import { ProjectMembersSection } from "../_components/ProjectMembersSection";

interface ProjectMemberLite {
  user_id: string;
  role: "admin" | "member" | "viewer";
}
interface MembersResp {
  members: ProjectMemberLite[];
}
interface TenantMembersResp {
  members: TenantMember[];
}

interface KeysResp {
  keys: ApiKey[];
}

interface UsageResp {
  requests: UsageRequest[];
  total: number;
}

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;

  const [project, setProject] = useState<Project | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [usage, setUsage] = useState<UsageRequest[]>([]);
  const [me, setMe] = useState<PortalUser | null>(null);
  // selfProjectRole is the caller's role on *this* project, derived
  // from the project_members fetch. Tenant Owner passes through as
  // "admin" via the canManage short-circuit below.
  const [selfProjectRole, setSelfProjectRole] = useState<
    "admin" | "member" | "viewer" | null
  >(null);
  const [tenantMembers, setTenantMembers] = useState<TenantMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [rotateTarget, setRotateTarget] = useState<ApiKey | null>(null);
  const [rotating, setRotating] = useState(false);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [p, k, u, meResp, pm, tm] = await Promise.all([
        api.get<Project>(`/portal/projects/${projectId}`),
        api.get<KeysResp>(`/portal/projects/${projectId}/keys`),
        api.get<UsageResp>(`/portal/me/usage?limit=200`),
        api.get<PortalUser>("/portal/me"),
        api.get<MembersResp>(
          `/portal/projects/${projectId}/members`
        ),
        api
          .get<TenantMembersResp>("/portal/team/members")
          .catch(() => ({ members: [] }) as TenantMembersResp),
      ]);
      setProject(p);
      setKeys(k.keys);
      // Server-side filter for usage-by-project is not in the MVP; for
      // now the stats row reflects all-user totals. When the backend
      // adds a project filter we'll narrow this. Flagged inline so it
      // isn't silently wrong in a later refactor.
      setUsage(u.requests);
      setMe(meResp);
      const selfRow = pm.members.find((m) => m.user_id === meResp.id);
      setSelfProjectRole(selfRow?.role ?? null);
      setTenantMembers(tm.members);
    } catch (err) {
      if (err instanceof PortalApiError) setError(err.message);
      else setError("Failed to load project.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (projectId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const stats = useMemo(() => {
    let tokens = 0;
    let requests = 0;
    for (const r of usage) {
      if (r.total_tokens != null) tokens += r.total_tokens;
      requests += 1;
    }
    return { tokens, requests };
  }, [usage]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setCreateError(null);
    if (!keyName.trim()) {
      setCreateError("Key name is required.");
      return;
    }
    setCreating(true);
    try {
      const newKey = await api.post<ApiKeyCreated>(
        `/portal/projects/${projectId}/keys`,
        { name: keyName.trim() }
      );
      setCreateOpen(false);
      setKeyName("");
      setCreatedKey(newKey);
      load();
    } catch (err) {
      if (err instanceof PortalApiError) setCreateError(err.message);
      else setCreateError("Failed to create key.");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.delete(`/portal/projects/${projectId}/keys/${deleteTarget.key_id}`);
      setDeleteTarget(null);
      load();
    } catch (err) {
      setDeleteError(err instanceof PortalApiError ? err.message : "Failed to revoke key.");
    } finally {
      setDeleting(false);
    }
  }

  async function confirmRotate() {
    if (!rotateTarget) return;
    setRotating(true);
    setRotateError(null);
    try {
      const rotated = await api.post<ApiKeyCreated>(
        `/portal/projects/${projectId}/keys/${rotateTarget.key_id}/rotate`
      );
      setRotateTarget(null);
      // Reuse the one-time key reveal modal — the response shape is
      // deliberately identical so the same UI works for create + rotate.
      setCreatedKey(rotated);
      load();
    } catch (err) {
      setRotateError(
        err instanceof PortalApiError ? err.message : "Failed to rotate key."
      );
    } finally {
      setRotating(false);
    }
  }

  function copyKey() {
    if (!createdKey) return;
    navigator.clipboard.writeText(createdKey.api_key).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  if (loading)
    return (
      <div className="space-y-6">
        <div className="skeleton" style={{ height: 48, width: 280, borderRadius: 12 }} />
        <StatCardRowSkeleton count={3} />
        <TableSkeleton rows={5} />
      </div>
    );

  if (error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div
          style={{
            background: "rgba(220,38,38,0.08)",
            border: "1px solid rgba(220,38,38,0.25)",
            color: "var(--accent-danger)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />

      <div className="flex items-start gap-4">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
          style={{
            background: "rgba(99,102,241,0.12)",
            color: "var(--accent-text)",
          }}
        >
          <FolderOpen size={24} strokeWidth={1.75} />
        </div>
        <div className="flex-1 min-w-0">
          <h1
            className="font-heading truncate"
            style={{
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            {project?.name}
          </h1>
          <p
            style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 4 }}
          >
            Created {formatDate(project?.created_at)}
          </p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatTile Icon={Hash} label="Total Requests" value={stats.requests.toLocaleString()} hint="Across all your projects" />
        <StatTile Icon={Zap} label="Tokens Used" value={formatTokens(stats.tokens)} hint="Across all your projects" />
        <StatTile Icon={Key} label="API Keys" value={keys.length.toString()} />
      </div>

      {/* API Keys */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-4">
          <h2
            className="font-heading"
            style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}
          >
            API Keys
          </h2>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus size={14} className="mr-1" strokeWidth={2} />
            Create Key
          </Button>
        </div>

        {keys.length === 0 ? (
          <div className="py-10 text-center">
            <div
              className="mx-auto w-12 h-12 rounded-full flex items-center justify-center"
              style={{
                background: "rgba(99,102,241,0.08)",
                border: "1px solid rgba(99,102,241,0.2)",
                color: "var(--accent-text)",
              }}
            >
              <Key size={26} strokeWidth={1.5} />
            </div>
            <h3
              className="mt-3 font-heading"
              style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}
            >
              No API keys yet
            </h3>
            <p
              className="mt-1 max-w-sm mx-auto"
              style={{ fontSize: 13, color: "var(--text-secondary)" }}
            >
              Create a key to start sending requests through the gateway.
            </p>
            <div className="mt-4">
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                Create your first key
              </Button>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full" style={{ fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <KeyTh>Name</KeyTh>
                  <KeyTh>Key</KeyTh>
                  <KeyTh>Status</KeyTh>
                  <KeyTh>Created</KeyTh>
                  <KeyTh>Last used</KeyTh>
                  <KeyTh align="right">{""}</KeyTh>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr
                    key={k.key_id}
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <KeyTd color="var(--text-primary)">{k.name}</KeyTd>
                    <KeyTd color="var(--text-secondary)" mono>
                      {k.key_prefix}…
                    </KeyTd>
                    <KeyTd>
                      <KeyStatus status={k.status} />
                    </KeyTd>
                    <KeyTd color="var(--text-secondary)">
                      {relativeTime(k.created_at)}
                    </KeyTd>
                    <KeyTd color="var(--text-secondary)">
                      {k.last_used_at ? relativeTime(k.last_used_at) : "Never"}
                    </KeyTd>
                    <KeyTd align="right">
                      {k.status === "active" ? (
                        <span
                          className="inline-flex items-center gap-3"
                          style={{ justifyContent: "flex-end" }}
                        >
                          <button
                            onClick={() => setRotateTarget(k)}
                            title="Rotate — mint a new key, keep old active 1h"
                            className="inline-flex items-center gap-1"
                            style={{
                              fontSize: 12,
                              fontWeight: 500,
                              color: "var(--accent-text)",
                              background: "transparent",
                              border: "none",
                              cursor: "pointer",
                            }}
                          >
                            <RotateCw size={12} />
                            Rotate
                          </button>
                          <button
                            onClick={() => setDeleteTarget(k)}
                            style={{
                              fontSize: 12,
                              fontWeight: 500,
                              color: "var(--accent-danger)",
                              background: "transparent",
                              border: "none",
                              cursor: "pointer",
                            }}
                          >
                            Revoke
                          </button>
                        </span>
                      ) : null}
                    </KeyTd>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Members section — 2026-04-24 portal-project-members task.
          canManage is derived from (tenant_role === 'owner') OR
          (caller's project role === 'admin'). The backend short-
          circuits the tenant-owner path to project-admin on every
          mutation, so client-side gating is purely for affordance. */}
      {me && project ? (
        <ProjectMembersSection
          projectId={projectId}
          currentUserId={me.id}
          canManage={
            me.tenant_role === "owner" || selfProjectRole === "admin"
          }
          tenantMembers={tenantMembers}
        />
      ) : null}

      {/* Create key modal */}
      <Modal
        open={createOpen}
        onClose={() => !creating && setCreateOpen(false)}
        title="Create API Key"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setCreateOpen(false)}
              disabled={creating}
            >
              Cancel
            </Button>
            <Button onClick={handleCreate} loading={creating}>
              Create Key
            </Button>
          </>
        }
      >
        <form onSubmit={handleCreate} className="space-y-4">
          {createError ? (
            <div
              style={{
                background: "rgba(220,38,38,0.08)",
                border: "1px solid rgba(220,38,38,0.25)",
                color: "var(--accent-danger)",
                fontSize: 13,
                padding: "8px 12px",
                borderRadius: 8,
              }}
            >
              {createError}
            </div>
          ) : null}
          <Input
            label="Key name"
            placeholder="e.g. Production Server"
            autoFocus
            required
            value={keyName}
            onChange={(e) => setKeyName(e.target.value)}
          />
        </form>
      </Modal>

      {/* One-time key reveal */}
      <Modal
        open={!!createdKey}
        onClose={() => setCreatedKey(null)}
        title="Your new API key"
        widthClass="max-w-lg"
        footer={
          <Button onClick={() => setCreatedKey(null)}>I&apos;ve saved it</Button>
        }
      >
        <div className="space-y-4">
          <div
            className="rounded-lg px-3 py-2.5 flex gap-2"
            style={{
              background: "rgba(245,158,11,0.1)",
              border: "1px solid rgba(245,158,11,0.3)",
              color: "var(--accent-warning)",
              fontSize: 13,
            }}
          >
            <AlertTriangle
              size={18}
              className="shrink-0 mt-0.5"
              style={{ color: "var(--accent-warning)" }}
            />
            <span>
              <strong>Save this key now.</strong> For security reasons it will{" "}
              <strong>not</strong> be shown again.
            </span>
          </div>
          <div className="flex gap-2">
            <code
              className="flex-1 rounded-lg px-3 py-2.5 break-all"
              style={{
                fontFamily: "var(--ff-mono)",
                fontSize: 12,
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border-default)",
                color: "var(--text-primary)",
              }}
            >
              {createdKey?.api_key}
            </code>
            <button
              onClick={copyKey}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-lg px-3"
              style={{
                background: "var(--accent-primary)",
                color: "#FFFFFF",
                fontSize: 13,
                fontWeight: 500,
                border: "1px solid var(--accent-primary)",
                cursor: "pointer",
              }}
            >
              {copied ? (
                <>
                  <Check size={14} /> Copied
                </>
              ) : (
                <>
                  <Copy size={14} /> Copy
                </>
              )}
            </button>
          </div>
        </div>
      </Modal>

      {/* Revoke confirmation */}
      <Modal
        open={!!deleteTarget}
        onClose={() => {
          if (!deleting) {
            setDeleteTarget(null);
            setDeleteError(null);
          }
        }}
        title="Revoke this key?"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setDeleteTarget(null);
                setDeleteError(null);
              }}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete} loading={deleting}>
              Revoke
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {deleteError ? (
            <div
              style={{
                background: "rgba(220,38,38,0.08)",
                border: "1px solid rgba(220,38,38,0.25)",
                color: "var(--accent-danger)",
                fontSize: 13,
                padding: "8px 12px",
                borderRadius: 8,
              }}
            >
              {deleteError}
            </div>
          ) : null}
          <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            Revoke{" "}
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {deleteTarget?.name}
            </span>
            ? Any request using this key will immediately fail authentication.
          </p>
        </div>
      </Modal>

      {/* Rotate confirmation */}
      <Modal
        open={!!rotateTarget}
        onClose={() => !rotating && setRotateTarget(null)}
        title="Rotate this key?"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setRotateTarget(null)}
              disabled={rotating}
            >
              Cancel
            </Button>
            <Button onClick={confirmRotate} loading={rotating}>
              Rotate key
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {rotateError ? (
            <div
              style={{
                background: "rgba(220,38,38,0.08)",
                border: "1px solid rgba(220,38,38,0.25)",
                color: "var(--accent-danger)",
                fontSize: 13,
                padding: "8px 12px",
                borderRadius: 8,
              }}
            >
              {rotateError}
            </div>
          ) : null}
          <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            Rotate{" "}
            <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
              {rotateTarget?.name}
            </span>
            ? You'll get a new key value. The old key will keep working
            for <strong>1 hour</strong> so you have time to roll
            deployments, then hard-expire.
          </p>
        </div>
      </Modal>
    </div>
  );
}

function BackLink() {
  const router = useRouter();
  return (
    <button
      onClick={() => router.push("/projects")}
      className="inline-flex items-center gap-1"
      style={{
        fontSize: 13,
        fontWeight: 500,
        color: "var(--accent-text)",
        background: "transparent",
        border: "none",
        cursor: "pointer",
      }}
    >
      <ChevronLeft size={16} />
      Back to projects
    </button>
  );
}

function StatTile({
  Icon,
  label,
  value,
  hint,
}: {
  Icon: typeof Zap;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card padding="md">
      <div
        className="w-9 h-9 rounded-lg flex items-center justify-center"
        style={{
          background: "rgba(99,102,241,0.14)",
          color: "var(--accent-text)",
        }}
      >
        <Icon size={18} strokeWidth={1.75} />
      </div>
      <p
        className="mt-3"
        style={{
          fontSize: 10.5,
          textTransform: "uppercase",
          letterSpacing: "0.14em",
          color: "var(--text-tertiary)",
          fontWeight: 600,
        }}
      >
        {label}
      </p>
      <p
        className="mt-1 font-heading"
        style={{
          fontSize: 24,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "var(--text-primary)",
        }}
      >
        {value}
      </p>
      {hint ? (
        <p
          title="Per-project breakdown coming soon"
          style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}
        >
          {hint}
        </p>
      ) : null}
    </Card>
  );
}

function KeyStatus({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const palette =
    s === "active"
      ? { bg: "rgba(16,185,129,0.12)", color: "var(--accent-success)" }
      : s === "revoked"
      ? { bg: "rgba(248,113,113,0.12)", color: "var(--accent-danger)" }
      : { bg: "var(--bg-elevated)", color: "var(--text-secondary)" };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full"
      style={{
        fontSize: 11,
        fontWeight: 500,
        background: palette.bg,
        color: palette.color,
      }}
    >
      {status}
    </span>
  );
}

function KeyTh({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        padding: "8px 16px 8px 0",
        textAlign: align,
        fontSize: 11,
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

function KeyTd({
  children,
  color = "var(--text-primary)",
  align = "left",
  mono = false,
}: {
  children: React.ReactNode;
  color?: string;
  align?: "left" | "right";
  mono?: boolean;
}) {
  return (
    <td
      style={{
        padding: "10px 16px 10px 0",
        color,
        textAlign: align,
        fontFamily: mono ? "var(--ff-mono)" : "inherit",
        fontSize: mono ? 12 : 13,
      }}
    >
      {children}
    </td>
  );
}
