"use client";

// Projects management page (Phase 4).
//
// Lists every project the user owns + lets them create new ones,
// edit the per-project instructions, attach a knowledge base, see
// the chats assigned to each project, and delete projects (chats
// survive — they are just un-assigned; the server does SET NULL).
//
// The page also hosts the "Personal instructions" panel — the
// user-wide custom_instructions_* preferences that apply to ALL
// chats regardless of project. They ride api.getPreferences /
// api.putPreferences and previously had no UI at all.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { timeAgo } from "@/components/workspace/workspaceUtil";
import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import type { Project } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

const INSTRUCTIONS_MAX = 8000;

export default function ProjectsPage() {
  const t = useT();
  const projects = useChatStore((s) => s.projects);
  const projectsLoaded = useChatStore((s) => s.projectsLoaded);
  const projectsError = useChatStore((s) => s.projectsError);
  const loadProjects = useChatStore((s) => s.loadProjects);
  const createProject = useChatStore((s) => s.createProject);
  const deleteProject = useChatStore((s) => s.deleteProject);
  const conversationsLoaded = useChatStore((s) => s.conversationsLoaded);
  const loadConversations = useChatStore((s) => s.loadConversations);

  const [createOpen, setCreateOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (!projectsLoaded) void loadProjects();
  }, [projectsLoaded, loadProjects]);

  // The per-project chat list filters the sidebar's conversation
  // store — make sure it's populated when the user lands here
  // directly (deep link / refresh).
  useEffect(() => {
    if (!conversationsLoaded) void loadConversations();
  }, [conversationsLoaded, loadConversations]);

  async function handleDeleteProject(p: Project) {
    if (!window.confirm(tOutside("projects.delete.confirm", { name: p.name }))) {
      return;
    }
    try {
      await deleteProject(p.id);
      toast(tOutside("projects.toast.deleted"));
      if (expandedId === p.id) setExpandedId(null);
    } catch {
      toast(tOutside("projects.toast.deleteFailed"));
    }
  }

  const hasProjects = projectsLoaded && !projectsError && projects.length > 0;
  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        {/* Subtle back link — mirrors the knowledge page. */}
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M15 6l-6 6 6 6" />
          </svg>
          {t("projects.back")}
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              {t("projects.title")}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("projects.subtitle")}
            </p>
          </div>
          {/* + New only renders when there's at least one project; the
              empty-state CTA covers the zero case. */}
          {hasProjects ? (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-transparent px-3.5 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
            >
              <PlusIcon />
              {t("projects.create")}
            </button>
          ) : null}
        </div>

        <div className="mt-8">
          {!projectsLoaded ? (
            <div className="flex flex-col gap-3" aria-label={t("common.loading")}>
              <Skeleton className="h-20 w-full rounded-xl" />
              <Skeleton className="h-20 w-full rounded-xl" />
              <Skeleton className="h-20 w-full rounded-xl" />
            </div>
          ) : projectsError ? (
            <div className="rounded-xl border border-border bg-bg-surface px-6 py-8 text-center">
              <p className="text-sm text-text-secondary">{projectsError}</p>
              <Button
                variant="secondary"
                className="mt-4"
                onClick={() => void loadProjects()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : projects.length === 0 ? (
            <EmptyProjectsState onCreate={() => setCreateOpen(true)} />
          ) : (
            <div className="flex flex-col gap-3">
              {projects.map((p) => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  expanded={expandedId === p.id}
                  onToggle={() =>
                    setExpandedId((id) => (id === p.id ? null : p.id))
                  }
                  onDelete={() => void handleDeleteProject(p)}
                />
              ))}
            </div>
          )}
        </div>

        <PersonalInstructionsPanel />
      </main>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={async (name, instructions) => {
          try {
            const p = await createProject(name, instructions);
            toast(tOutside("projects.toast.created", { name: p.name }));
            setCreateOpen(false);
            setExpandedId(p.id);
          } catch {
            toast(tOutside("projects.toast.createFailed"));
          }
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyProjectsState({ onCreate }: { onCreate: () => void }) {
  const t = useT();
  return (
    <div className="rounded-xl border border-dashed border-border bg-transparent px-8 py-16 text-center">
      <svg
        width={48}
        height={48}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className="mx-auto mb-6 text-text-muted"
      >
        <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
        <path d="M8 13h8M8 16h5" />
      </svg>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        {t("projects.empty.title")}
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        {t("projects.empty.body")}
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="focus-ring mt-6 inline-flex items-center gap-1.5 rounded-lg border border-border bg-transparent px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
      >
        <PlusIcon />
        {t("projects.create.full")}
      </button>
    </div>
  );
}

function PlusIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Project row + expanded management panel
// ---------------------------------------------------------------------------

function ProjectCard({
  project,
  expanded,
  onToggle,
  onDelete,
}: {
  project: Project;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  return (
    <section
      className={`overflow-hidden rounded-xl border bg-bg-surface transition-colors ${
        expanded ? "border-border-strong" : "border-border hover:border-border-strong"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-start justify-between gap-4 p-5 text-left"
      >
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-medium text-text-primary">
            {project.name}
          </h3>
          <p className="mt-3 text-xs text-text-muted">
            {project.conversation_count}{" "}
            {project.conversation_count === 1
              ? t("projects.unit.chat")
              : t("projects.unit.chats")}
            {" · "}
            {t("projects.updatedAgo", { time: timeAgo(project.updated_at) })}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 text-sm text-text-secondary transition-colors group-hover:text-text-primary">
          <span>
            {expanded ? t("projects.card.close") : t("projects.card.manage")}
          </span>
          {expanded ? <ChevronIcon up /> : <ArrowRightIcon />}
        </span>
      </button>
      {expanded ? (
        <ProjectDetailPanel project={project} onDelete={onDelete} />
      ) : null}
    </section>
  );
}

function ArrowRightIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function ChevronIcon({ up }: { up: boolean }) {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={`shrink-0 text-text-muted transition-transform ${up ? "rotate-180" : ""}`}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function ProjectDetailPanel({
  project,
  onDelete,
}: {
  project: Project;
  onDelete: () => void;
}) {
  const t = useT();
  const router = useRouter();
  const updateProject = useChatStore((s) => s.updateProject);
  const setProjectKB = useChatStore((s) => s.setProjectKnowledgeBase);
  const conversations = useChatStore((s) => s.conversations);
  const setNextConversationProjectId = useChatStore(
    (s) => s.setNextConversationProjectId,
  );

  const knowledgeBases = useKnowledgeStore((s) => s.knowledgeBases);
  const kbsLoaded = useKnowledgeStore((s) => s.loaded);
  const kbsLoading = useKnowledgeStore((s) => s.loading);
  const loadKBs = useKnowledgeStore((s) => s.loadKnowledgeBases);

  const [name, setName] = useState(project.name);
  const [instructions, setInstructions] = useState(project.instructions);
  const [saving, setSaving] = useState(false);
  const [kbSaving, setKbSaving] = useState(false);

  useEffect(() => {
    if (!kbsLoaded && !kbsLoading) void loadKBs();
  }, [kbsLoaded, kbsLoading, loadKBs]);

  const dirty =
    name.trim() !== project.name || instructions !== project.instructions;
  const tooLong = instructions.length > INSTRUCTIONS_MAX;

  async function handleSave() {
    const trimmed = name.trim();
    if (!trimmed || tooLong) return;
    setSaving(true);
    try {
      await updateProject(project.id, {
        name: trimmed,
        instructions,
      });
      toast(tOutside("projects.toast.saved"));
    } catch {
      toast(tOutside("projects.toast.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleKBChange(value: string) {
    setKbSaving(true);
    try {
      await setProjectKB(project.id, value === "" ? null : value);
      toast(tOutside("projects.toast.kbSaved"));
    } catch {
      toast(tOutside("projects.toast.kbSaveFailed"));
    } finally {
      setKbSaving(false);
    }
  }

  const projectChats = conversations.filter((c) => c.project_id === project.id);

  return (
    <div className="flex flex-col gap-5 border-t border-border/60 bg-bg-base/50 px-5 py-5">
      {/* Name + instructions */}
      <Input
        label={t("projects.name.label")}
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={255}
      />
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor={`proj-instr-${project.id}`}
          className="text-xs font-medium uppercase tracking-wider text-text-secondary"
        >
          {t("projects.instructions.label")}
        </label>
        <textarea
          id={`proj-instr-${project.id}`}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={5}
          placeholder={t("projects.instructions.placeholder")}
          className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
        />
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-text-muted">
            {t("projects.instructions.hint")}
          </span>
          <span
            className={tooLong ? "text-status-danger" : "text-text-muted"}
          >
            {instructions.length} / {INSTRUCTIONS_MAX}
          </span>
        </div>
      </div>
      <div>
        <Button
          onClick={() => void handleSave()}
          loading={saving}
          disabled={!dirty || !name.trim() || tooLong}
        >
          {saving ? t("instr.saving") : t("instr.save")}
        </Button>
      </div>

      {/* Knowledge base attach */}
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor={`proj-kb-${project.id}`}
          className="text-xs font-medium uppercase tracking-wider text-text-secondary"
        >
          {t("projects.kb.label")}
        </label>
        <select
          id={`proj-kb-${project.id}`}
          value={project.knowledge_base_id ?? ""}
          disabled={kbSaving || !kbsLoaded}
          onChange={(e) => void handleKBChange(e.target.value)}
          className="rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40 disabled:opacity-60"
        >
          <option value="">{t("projects.kb.none")}</option>
          {knowledgeBases.map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name}
            </option>
          ))}
        </select>
        <span className="text-[11px] text-text-muted">
          {t("projects.kb.hint")}
        </span>
      </div>

      {/* Chats in this project */}
      <div>
        <div className="mb-2 flex items-center justify-between gap-2">
          <h4 className="text-xs font-medium uppercase tracking-wider text-text-secondary">
            {t("projects.chats.title")}
          </h4>
          <button
            type="button"
            onClick={() => {
              setNextConversationProjectId(project.id);
              router.push("/chat");
            }}
            className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-primary hover:bg-bg-elevated"
          >
            <PlusIcon />
            {t("projects.newChat")}
          </button>
        </div>
        {projectChats.length === 0 ? (
          <p className="py-2 text-xs text-text-muted">
            {t("projects.chats.empty")}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {projectChats.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => router.push(`/chat/${c.id}`)}
                  className="focus-ring flex w-full items-center gap-3 rounded-md border border-border bg-bg-surface px-3 py-2 text-left text-xs text-text-primary hover:bg-bg-elevated"
                >
                  <span className="min-w-0 flex-1 truncate">
                    {c.title || t("conv.untitled")}
                  </span>
                  <span className="shrink-0 text-text-muted">
                    {timeAgo(c.updated_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Delete */}
      <div className="border-t border-border/60 pt-3">
        <button
          type="button"
          onClick={onDelete}
          className="rounded-md px-2 py-1.5 text-xs text-status-danger hover:bg-status-danger/10"
        >
          {t("projects.delete")}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create-project modal
// ---------------------------------------------------------------------------

function CreateProjectModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, instructions: string) => Promise<void>;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setName("");
      setInstructions("");
      setSubmitting(false);
    }
  }, [open]);

  async function submit() {
    if (!name.trim() || instructions.length > INSTRUCTIONS_MAX) return;
    setSubmitting(true);
    try {
      await onCreate(name.trim(), instructions);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("projects.create")}
      description={t("projects.create.description")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => void submit()}
            loading={submitting}
            disabled={!name.trim()}
          >
            {t("projects.create.full")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label={t("projects.name.label")}
          placeholder={t("projects.name.placeholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={255}
          autoFocus
        />
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="new-proj-instr"
            className="text-xs font-medium uppercase tracking-wider text-text-secondary"
          >
            {t("projects.instructions.label")}
          </label>
          <textarea
            id="new-proj-instr"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={4}
            placeholder={t("projects.instructions.placeholder")}
            className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
          />
          <span className="self-end text-[11px] text-text-muted">
            {instructions.length} / {INSTRUCTIONS_MAX}
          </span>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Personal instructions (user-wide custom instructions)
// ---------------------------------------------------------------------------

function PersonalInstructionsPanel() {
  const t = useT();
  const [about, setAbout] = useState("");
  const [respond, setRespond] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  // Snapshot of the last-saved values so the save button only lights
  // up when something actually changed.
  const [saved, setSaved] = useState({ about: "", respond: "" });

  useEffect(() => {
    let cancelled = false;
    api
      .getPreferences()
      .then((prefs) => {
        if (cancelled) return;
        const a = prefs?.custom_instructions_about ?? "";
        const r = prefs?.custom_instructions_respond ?? "";
        setAbout(a);
        setRespond(r);
        setSaved({ about: a, respond: r });
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
        setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty = about !== saved.about || respond !== saved.respond;

  async function handleSave() {
    setSaving(true);
    try {
      await api.putPreferences({
        custom_instructions_about: about,
        custom_instructions_respond: respond,
      });
      setSaved({ about, respond });
      toast(tOutside("projects.personal.toast.saved"));
    } catch {
      toast(tOutside("projects.personal.toast.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="mt-10 rounded-xl border border-border bg-bg-surface p-5">
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        {t("projects.personal.title")}
      </h2>
      <p className="mt-1 text-sm text-text-secondary">
        {t("projects.personal.subtitle")}
      </p>

      {!loaded ? (
        <div className="mt-4 flex flex-col gap-3">
          <Skeleton className="h-16 w-full rounded-md" />
          <Skeleton className="h-16 w-full rounded-md" />
        </div>
      ) : loadError ? (
        <p className="mt-4 text-sm text-text-muted">
          {t("projects.personal.loadFailed")}
        </p>
      ) : (
        <div className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="personal-about"
              className="text-xs font-medium uppercase tracking-wider text-text-secondary"
            >
              {t("projects.personal.about.label")}
            </label>
            <textarea
              id="personal-about"
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={3}
              placeholder={t("instr.about.placeholder")}
              className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="personal-respond"
              className="text-xs font-medium uppercase tracking-wider text-text-secondary"
            >
              {t("projects.personal.respond.label")}
            </label>
            <textarea
              id="personal-respond"
              value={respond}
              onChange={(e) => setRespond(e.target.value)}
              rows={3}
              placeholder={t("instr.respond.placeholder")}
              className="scrollbar-thin resize-y rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40"
            />
          </div>
          <div>
            <Button
              onClick={() => void handleSave()}
              loading={saving}
              disabled={!dirty}
            >
              {saving ? t("instr.saving") : t("instr.save")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
