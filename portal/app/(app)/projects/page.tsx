"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { FolderPlus } from "lucide-react";

import { Button } from "@/components/Button";
import { Input } from "@/components/Input";
import { Modal } from "@/components/Modal";
import { CardGridSkeleton } from "@/components/skeletons/CardGridSkeleton";
import { api, PortalApiError } from "@/lib/api";
import type { PortalUser, Project } from "@/lib/types";
import EmptyState from "../_components/EmptyState";
import PageHeader from "../_components/PageHeader";
import ProjectCard from "./_components/ProjectCard";

interface ProjectsResp {
  projects: Project[];
}

interface KeysResp {
  keys: { key_id: string }[];
}

interface ProjectStats {
  keys: number;
}

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [statsMap, setStatsMap] = useState<Record<string, ProjectStats>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<PortalUser | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [renameTarget, setRenameTarget] = useState<Project | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState(false);
  // Inline error for the rename/delete modals (only one is open at a time).
  const [actionError, setActionError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const [r, meResp] = await Promise.all([
        api.get<ProjectsResp>("/portal/projects"),
        api.get<PortalUser>("/portal/me"),
      ]);
      setProjects(r.projects);
      setMe(meResp);

      // Fetch key counts per project in parallel. Requests counts are
      // not yet per-project on the backend; leave at 0 for now.
      const stats: Record<string, ProjectStats> = {};
      await Promise.all(
        r.projects.map(async (p) => {
          try {
            const k = await api.get<KeysResp>(`/portal/projects/${p.id}/keys`);
            stats[p.id] = { keys: k.keys.length };
          } catch {
            stats[p.id] = { keys: 0 };
          }
        })
      );
      setStatsMap(stats);
    } catch (err) {
      if (err instanceof PortalApiError) setError(err.message);
      else setError("Failed to load projects.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!newName.trim()) {
      setSubmitError("Project name is required.");
      return;
    }
    setSubmitting(true);
    try {
      await api.post<Project>("/portal/projects", { name: newName.trim() });
      setCreateOpen(false);
      setNewName("");
      load();
    } catch (err) {
      if (err instanceof PortalApiError) setSubmitError(err.message);
      else setSubmitError("Failed to create project.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRename(e: FormEvent) {
    e.preventDefault();
    if (!renameTarget) return;
    if (!renameValue.trim()) return;
    setRenameSaving(true);
    setActionError(null);
    try {
      await api.patch(`/portal/projects/${renameTarget.id}`, {
        name: renameValue.trim(),
      });
      setRenameTarget(null);
      load();
    } catch (err) {
      setActionError(err instanceof PortalApiError ? err.message : "Rename failed");
    } finally {
      setRenameSaving(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setActionError(null);
    try {
      await api.delete(`/portal/projects/${deleteTarget.id}`);
      setDeleteTarget(null);
      load();
    } catch (err) {
      setActionError(err instanceof PortalApiError ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projects"
        subtitle="Group your API keys by application or environment."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <FolderPlus size={16} className="mr-1.5" strokeWidth={1.75} />
            New Project
          </Button>
        }
      />

      {error ? (
        <div
          style={{
            background: "rgba(220, 38, 38, 0.08)",
            border: "1px solid rgba(220, 38, 38, 0.25)",
            color: "var(--accent-danger)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="space-y-6">
          <div className="skeleton" style={{ height: 32, width: 220, borderRadius: 8 }} />
          <CardGridSkeleton />
        </div>
      ) : projects.length === 0 ? (
        // Empty-state copy branches on tenant role. A tenant-owner with
        // no projects is a fresh tenant; a plain member with no
        // projects is a user whose team hasn't granted access yet —
        // different framing nudges the right next action.
        me?.tenant_role === "member" ? (
          <EmptyState
            icon={<FolderPlus className="w-7 h-7" strokeWidth={1.5} />}
            title="No projects yet"
            description="Ask your team admin to grant access, or create one of your own."
            action={{ label: "Create project", onClick: () => setCreateOpen(true) }}
          />
        ) : (
          <EmptyState
            icon={<FolderPlus className="w-7 h-7" strokeWidth={1.5} />}
            title="No projects yet"
            description="Create your first project to organize API keys by application or environment."
            action={{ label: "Create project", onClick: () => setCreateOpen(true) }}
            secondaryAction={{ label: "View docs", href: "/docs" }}
          />
        )
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              stats={statsMap[p.id] ?? { keys: 0 }}
              onRename={() => {
                setRenameTarget(p);
                setRenameValue(p.name);
              }}
              onDelete={() => setDeleteTarget(p)}
              onOpen={() => router.push(`/projects/${p.id}`)}
            />
          ))}
        </div>
      )}

      {/* Create modal */}
      <Modal
        open={createOpen}
        onClose={() => {
          if (!submitting) {
            setCreateOpen(false);
            setSubmitError(null);
            setNewName("");
          }
        }}
        title="New Project"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setCreateOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button onClick={handleCreate} loading={submitting}>
              Create Project
            </Button>
          </>
        }
      >
        <form onSubmit={handleCreate} className="space-y-4">
          {submitError ? (
            <div
              style={{
                background: "rgba(220, 38, 38, 0.08)",
                border: "1px solid rgba(220, 38, 38, 0.25)",
                color: "var(--accent-danger)",
                fontSize: 13,
                padding: "8px 12px",
                borderRadius: 8,
              }}
            >
              {submitError}
            </div>
          ) : null}
          <Input
            label="Project name"
            placeholder="e.g. Production Chatbot"
            autoFocus
            required
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
        </form>
      </Modal>

      {/* Rename modal */}
      <Modal
        open={!!renameTarget}
        onClose={() => {
          if (!renameSaving) {
            setRenameTarget(null);
            setActionError(null);
          }
        }}
        title="Rename project"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setRenameTarget(null);
                setActionError(null);
              }}
              disabled={renameSaving}
            >
              Cancel
            </Button>
            <Button onClick={handleRename} loading={renameSaving}>
              Save
            </Button>
          </>
        }
      >
        <form onSubmit={handleRename} className="space-y-4">
          {actionError ? (
            <div
              style={{
                background: "rgba(220, 38, 38, 0.08)",
                border: "1px solid rgba(220, 38, 38, 0.25)",
                color: "var(--accent-danger)",
                fontSize: 13,
                padding: "8px 12px",
                borderRadius: 8,
              }}
            >
              {actionError}
            </div>
          ) : null}
          <Input
            label="Project name"
            autoFocus
            required
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
          />
        </form>
      </Modal>

      {/* Delete modal */}
      <Modal
        open={!!deleteTarget}
        onClose={() => {
          if (!deleting) {
            setDeleteTarget(null);
            setActionError(null);
          }
        }}
        title="Delete project?"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setDeleteTarget(null);
                setActionError(null);
              }}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete} loading={deleting}>
              Delete
            </Button>
          </>
        }
      >
        {actionError ? (
          <div
            style={{
              background: "rgba(220, 38, 38, 0.08)",
              border: "1px solid rgba(220, 38, 38, 0.25)",
              color: "var(--accent-danger)",
              fontSize: 13,
              padding: "8px 12px",
              borderRadius: 8,
              marginBottom: 12,
            }}
          >
            {actionError}
          </div>
        ) : null}
        <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          Delete{" "}
          <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
            {deleteTarget?.name}
          </span>
          ? All API keys in this project will be revoked immediately. This cannot
          be undone.
        </p>
      </Modal>
    </div>
  );
}

