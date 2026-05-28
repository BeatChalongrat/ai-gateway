"use client";

// Memory Settings page (S1 — Personal Memory Core UI).
//
// Layout mirrors the Knowledge Base management page at
// /chat/knowledge: subtle back-link, centred max-w-3xl column,
// muted neutral surface, no chrome. The page owns its own list
// state (no global store yet — memories rarely change and the
// page is rarely open) and re-fetches on every mount.
//
// Three sections, separated by muted dividers:
//   1. Memories — list + count + Add button + empty/loading/error
//   2. Temporary Chat launcher
//   3. Danger Zone — clear all

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  AlertTriangle,
  Brain,
  Plus,
  Trash2,
} from "lucide-react";

import { MemoryCard } from "@/components/chat/MemoryCard";
import { MemoryFormDialog } from "@/components/chat/MemoryFormDialog";
import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";
import type { Memory } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

export default function MemorySettingsPage() {
  const router = useRouter();
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Memory | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Memory | null>(null);

  // Lookup table so MemoryCard's source line can resolve a source
  // conversation id to its title. Falls back to "another
  // conversation" inside the card when the id isn't in the user's
  // currently-loaded list.
  const conversations = useChatStore((s) => s.conversations);
  const setNextConversationTemporary = useChatStore(
    (s) => s.setNextConversationTemporary,
  );
  const resolveConversationTitle = useCallback(
    (id: string) => conversations.find((c) => c.id === id)?.title,
    [conversations],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listMemories({ limit: 100 });
      setMemories(res.memories);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : "Couldn't load memories.";
      setError(msg);
      setMemories(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function upsertLocal(m: Memory) {
    setMemories((prev) => {
      if (!prev) return [m];
      const next = prev.filter((x) => x.id !== m.id);
      next.unshift(m);
      return sortMemories(next);
    });
  }

  async function handleDelete(id: string) {
    const target = memories?.find((m) => m.id === id) ?? null;
    if (target) setPendingDelete(target);
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target) return;
    setPendingDelete(null);
    try {
      await api.deleteMemory(target.id);
      setMemories((prev) => prev?.filter((m) => m.id !== target.id) ?? null);
      toast("Memory deleted.");
    } catch {
      toast("Couldn't delete memory.");
    }
  }

  async function handleTogglePin(id: string, pinned: boolean) {
    try {
      const updated = await api.pinMemory(id, pinned);
      setMemories((prev) => {
        if (!prev) return prev;
        const next = prev.map((m) => (m.id === id ? updated : m));
        return sortMemories(next);
      });
    } catch {
      toast("Couldn't update pin.");
    }
  }

  async function handleClearAll() {
    setConfirmClear(false);
    try {
      const { deleted_count } = await api.clearAllMemories();
      setMemories([]);
      toast(
        deleted_count > 0
          ? `Cleared ${deleted_count} ${deleted_count === 1 ? "memory" : "memories"}.`
          : "No memories to clear.",
      );
    } catch {
      toast("Couldn't clear memories.");
    }
  }

  function startTemporaryChat() {
    setNextConversationTemporary(true);
    useChatStore.getState().setActive(null);
    router.push("/chat");
    toast("Starting a temporary chat — memory is off for this one.");
  }

  const count = memories?.length ?? 0;

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 pt-8 pb-12">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackArrow />
          <span>Back to chat</span>
        </Link>

        <header className="mt-6 flex items-start gap-3">
          <span
            aria-hidden
            className="mt-0.5 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border-subtle text-text-tertiary"
          >
            <Brain className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold tracking-tight text-text-primary">
              Memory
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              SRAN Chat remembers facts from your conversations to personalize
              future responses.
            </p>
          </div>
        </header>

        <section className="mt-8">
          <div className="flex items-baseline justify-between">
            <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted">
              {count === 1 ? "1 saved memory" : `${count} saved memories`}
            </h2>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated"
            >
              <Plus className="h-3.5 w-3.5" />
              Add memory
            </button>
          </div>

          <div className="mt-3">
            {loading && memories === null ? (
              <LoadingSkeleton />
            ) : error ? (
              <ErrorState message={error} onRetry={() => void load()} />
            ) : !memories || memories.length === 0 ? (
              <EmptyState onAdd={() => setCreateOpen(true)} />
            ) : (
              <div className="space-y-2">
                {memories.map((m) => (
                  <MemoryCard
                    key={m.id}
                    memory={m}
                    onEdit={(mem) => setEditing(mem)}
                    onDelete={(id) => void handleDelete(id)}
                    onTogglePin={(id, pinned) => void handleTogglePin(id, pinned)}
                    resolveConversationTitle={resolveConversationTitle}
                  />
                ))}
              </div>
            )}
          </div>
        </section>

        <Divider />

        <section>
          <h2 className="text-sm font-semibold text-text-primary">
            Temporary chat
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            Start a conversation that doesn't use or create memory. Useful
            when you don't want a particular topic feeding into your saved
            facts.
          </p>
          <button
            type="button"
            onClick={startTemporaryChat}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <Brain className="h-3.5 w-3.5" />
            Start temporary chat
          </button>
        </section>

        <Divider />

        <section>
          <h2 className="text-sm font-semibold text-status-danger">
            Danger zone
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            Clear every saved memory. The chat history stays — only the
            cross-conversation facts the AI remembers about you are removed.
          </p>
          <button
            type="button"
            onClick={() => setConfirmClear(true)}
            disabled={!memories || memories.length === 0}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-status-danger/40 bg-transparent px-3 py-1.5 text-xs text-status-danger transition-colors hover:bg-status-danger/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear all memories
          </button>
        </section>
      </main>

      <MemoryFormDialog
        open={createOpen}
        mode="create"
        onClose={() => setCreateOpen(false)}
        onSaved={(m) => upsertLocal(m)}
      />
      <MemoryFormDialog
        open={editing !== null}
        mode="edit"
        initial={editing}
        onClose={() => setEditing(null)}
        onSaved={(m) => upsertLocal(m)}
      />
      <ConfirmDialog
        open={confirmClear}
        title="Clear all memories?"
        body="This will remove every saved memory for your account. This action cannot be undone."
        confirmLabel="Clear all"
        danger
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => void handleClearAll()}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this memory?"
        body={pendingDelete ? `"${pendingDelete.content}"` : ""}
        confirmLabel="Delete"
        danger
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

// ── Section primitives ─────────────────────────────────────────

function Divider() {
  return <div className="my-8 h-px bg-border-subtle" />;
}

function BackArrow() {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M15 6l-6 6 6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-20 animate-pulse rounded-lg border border-border-subtle bg-bg-surface"
        />
      ))}
    </div>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-surface px-6 py-10 text-center">
      <Brain className="mx-auto h-8 w-8 text-text-tertiary" />
      <p className="mt-3 text-sm text-text-secondary">No memories saved yet</p>
      <p className="mt-1 text-xs text-text-muted">
        Tell the AI &ldquo;จำไว้ว่า…&rdquo; or &ldquo;remember that…&rdquo; in
        any chat to save facts, or add them manually.
      </p>
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring mx-auto mt-4 inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated"
      >
        <Plus className="h-3.5 w-3.5" />
        Add memory
      </button>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-status-danger/40 bg-status-danger/5 px-6 py-8 text-center">
      <AlertTriangle className="mx-auto h-7 w-7 text-status-danger" />
      <p className="mt-2 text-sm text-text-secondary">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="focus-ring mt-3 rounded-md px-2 py-1 text-xs text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        Try again
      </button>
    </div>
  );
}

// ── Confirm dialog (inlined — only the memory page uses it today) ──

function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      maxWidth={420}
      footer={
        <>
          <button
            type="button"
            onClick={onCancel}
            className="focus-ring rounded-lg border border-border bg-transparent px-4 py-2 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`focus-ring rounded-lg px-6 py-2 text-sm font-medium transition-opacity hover:opacity-90 ${
              danger
                ? "bg-status-danger text-white"
                : "bg-text-primary text-bg-base"
            }`}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <p className="text-sm text-text-secondary">{body}</p>
    </Modal>
  );
}

// ── Pure helpers ───────────────────────────────────────────────

// sortMemories — mirror the server's ORDER BY (pinned first, then
// importance desc, then updated_at desc) so optimistic mutations
// land in the same slot the next /memories refresh would put them.
function sortMemories(rows: Memory[]): Memory[] {
  return [...rows].sort((a, b) => {
    if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1;
    if (a.importance_score !== b.importance_score) {
      return b.importance_score - a.importance_score;
    }
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}
