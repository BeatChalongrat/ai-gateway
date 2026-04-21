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
  Zap,
} from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Input } from "@/components/Input";
import { Modal } from "@/components/Modal";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, formatTokens, relativeTime } from "@/lib/format";
import type { ApiKey, ApiKeyCreated, Project, UsageRequest } from "@/lib/types";

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

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [p, k, u] = await Promise.all([
        api.get<Project>(`/portal/projects/${projectId}`),
        api.get<KeysResp>(`/portal/projects/${projectId}/keys`),
        api.get<UsageResp>(`/portal/me/usage?limit=200`),
      ]);
      setProject(p);
      setKeys(k.keys);
      // Server-side filter for usage-by-project is not in the MVP; for
      // now the stats row reflects all-user totals. When the backend
      // adds a project filter we'll narrow this. Flagged inline so it
      // isn't silently wrong in a later refactor.
      setUsage(u.requests);
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
    try {
      await api.delete(`/portal/projects/${projectId}/keys/${deleteTarget.key_id}`);
      setDeleteTarget(null);
      load();
    } catch (err) {
      alert(err instanceof PortalApiError ? err.message : "Failed to revoke key.");
    } finally {
      setDeleting(false);
    }
  }

  function copyKey() {
    if (!createdKey) return;
    navigator.clipboard.writeText(createdKey.api_key).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  if (loading) return <PageSpinner />;

  if (error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div className="rounded-lg bg-red-50 border border-red-200 text-brand-red text-sm px-3 py-2">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />

      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-xl bg-brand-navy/10 text-brand-navy flex items-center justify-center shrink-0">
          <FolderOpen size={24} strokeWidth={1.75} />
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-heading font-bold truncate">
            {project?.name}
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            Created {formatDate(project?.created_at)}
          </p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatTile Icon={Hash} label="Total Requests" value={stats.requests.toLocaleString()} />
        <StatTile Icon={Zap} label="Tokens Used" value={formatTokens(stats.tokens)} />
        <StatTile Icon={Key} label="API Keys" value={keys.length.toString()} />
      </div>

      {/* API Keys */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-heading font-semibold">API Keys</h2>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus size={14} className="mr-1" strokeWidth={2} />
            Create Key
          </Button>
        </div>

        {keys.length === 0 ? (
          <div className="py-10 text-center">
            <div className="mx-auto w-12 h-12 rounded-full bg-gray-50 flex items-center justify-center text-gray-300">
              <Key size={26} strokeWidth={1.5} />
            </div>
            <h3 className="mt-3 text-base font-heading font-semibold text-gray-900">
              No API keys yet
            </h3>
            <p className="mt-1 text-sm text-gray-500 max-w-sm mx-auto">
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
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wider text-gray-500 border-b border-gray-200">
                  <th className="py-2 pr-4 font-medium">Name</th>
                  <th className="py-2 pr-4 font-medium">Key</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Created</th>
                  <th className="py-2 pr-4 font-medium">Last used</th>
                  <th className="py-2 pr-4 font-medium text-right"></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.key_id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                    <td className="py-2 pr-4 text-gray-900">{k.name}</td>
                    <td className="py-2 pr-4 font-mono text-xs text-gray-700">
                      {k.key_prefix}…
                    </td>
                    <td className="py-2 pr-4">
                      <KeyStatus status={k.status} />
                    </td>
                    <td className="py-2 pr-4 text-gray-600">
                      {relativeTime(k.created_at)}
                    </td>
                    <td className="py-2 pr-4 text-gray-600">
                      {k.last_used_at ? relativeTime(k.last_used_at) : "Never"}
                    </td>
                    <td className="py-2 pr-4 text-right">
                      {k.status === "active" ? (
                        <button
                          onClick={() => setDeleteTarget(k)}
                          className="text-xs font-medium text-brand-red hover:underline"
                        >
                          Revoke
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

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
            <div className="rounded-lg bg-red-50 border border-red-200 text-brand-red text-sm px-3 py-2">
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
          <div className="rounded-lg bg-amber-50 border border-amber-200 text-amber-900 px-3 py-2.5 text-sm flex gap-2">
            <AlertTriangle size={18} className="shrink-0 mt-0.5 text-amber-600" />
            <span>
              <strong>Save this key now.</strong> For security reasons it will{" "}
              <strong>not</strong> be shown again.
            </span>
          </div>
          <div className="flex gap-2">
            <code className="flex-1 font-mono text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2.5 break-all">
              {createdKey?.api_key}
            </code>
            <button
              onClick={copyKey}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-brand-navy text-white px-3 text-sm font-medium hover:bg-brand-dark"
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
        onClose={() => !deleting && setDeleteTarget(null)}
        title="Revoke this key?"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteTarget(null)}
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
        <p className="text-sm text-gray-700">
          Revoke <span className="font-semibold">{deleteTarget?.name}</span>? Any request using
          this key will immediately fail authentication.
        </p>
      </Modal>
    </div>
  );
}

function BackLink() {
  const router = useRouter();
  return (
    <button
      onClick={() => router.push("/projects")}
      className="inline-flex items-center gap-1 text-sm font-medium text-brand-navy hover:text-brand-blue"
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
}: {
  Icon: typeof Zap;
  label: string;
  value: string;
}) {
  return (
    <Card padding="md" className="card-hover">
      <div className="w-9 h-9 rounded-lg bg-brand-blue/10 text-brand-blue flex items-center justify-center">
        <Icon size={18} strokeWidth={1.75} />
      </div>
      <p className="mt-3 text-xs uppercase tracking-widest text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-heading font-bold text-gray-900">{value}</p>
    </Card>
  );
}

function KeyStatus({ status }: { status: string }) {
  const s = (status || "").toLowerCase();
  const color =
    s === "active"
      ? "bg-green-100 text-green-800"
      : s === "revoked"
      ? "bg-red-100 text-red-800"
      : "bg-gray-100 text-gray-700";
  return (
    <span className={["inline-block px-2 py-0.5 rounded-full text-xs font-medium", color].join(" ")}>
      {status}
    </span>
  );
}
