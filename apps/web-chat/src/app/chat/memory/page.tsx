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

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  AlertTriangle,
  Brain,
  Download,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";

import { MemoryCard } from "@/components/chat/MemoryCard";
import { MemoryFormDialog } from "@/components/chat/MemoryFormDialog";
import { LocalNotesSection } from "@/components/chat/LocalNotesSection";
import { PendingPreferenceCard } from "@/components/chat/PendingPreferenceCard";
import { useMemoryPendingStore } from "@/hooks/useMemoryPendingCount";
import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Memory } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

export default function MemorySettingsPage() {
  const router = useRouter();
  const t = useT();
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

  const [pending, setPending] = useState<Memory[]>([]);
  const [pendingBusy, setPendingBusy] = useState<string | null>(null);

  // S9 — local-only filter + export/purge busy states. The
  // search is intentionally client-side: at 200 memories
  // (default max), an in-page contains() is faster than a
  // backend round-trip on every keystroke.
  const [searchQuery, setSearchQuery] = useState("");
  const [exporting, setExporting] = useState(false);
  const [purging, setPurging] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState(false);
  // S8 — keep the sidebar badge + chat-header dot honest the
  // instant the user acts on a suggestion. refresh() does a fresh
  // GET /memories/pending-count rather than decrementing the
  // local cache so a concurrent extraction firing elsewhere
  // doesn't desync.
  const refreshPendingCount = useMemoryPendingStore((s) => s.refresh);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Two parallel fetches — active rows for the main list +
      // pending_confirmation rows for the S8 suggestions section
      // that sits above it. Each runs in its own try/catch so a
      // failure on one source doesn't blank the other.
      const [active, pendingRes] = await Promise.all([
        api.listMemories({ limit: 100 }),
        api
          .listMemories({ status: "pending_confirmation", limit: 50 })
          .catch(() => ({ memories: [], total: 0 })),
      ]);
      setMemories(active.memories);
      setPending(pendingRes.memories);
    } catch (err) {
      // 404 from the list endpoint just means "no memories yet"
      // (or an early migration race) — surface the empty state,
      // not an error banner.
      if (err instanceof ApiError && err.status === 404) {
        setMemories([]);
        setPending([]);
        return;
      }
      const msg =
        err instanceof ApiError ? err.message : t("memory.error.loadFailed");
      setError(msg);
      setMemories(null);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleConfirmPending(id: string) {
    setPendingBusy(id);
    try {
      const updated = await api.confirmMemory(id, true);
      setPending((prev) => prev.filter((m) => m.id !== id));
      // Promoted to active → splice into the main list (sorted).
      setMemories((prev) => {
        if (!prev) return [updated];
        const next = prev.filter((x) => x.id !== id);
        next.unshift(updated);
        return sortMemories(next);
      });
      toast(t("memory.toast.preferenceConfirmed"));
      void refreshPendingCount();
    } catch {
      toast(t("memory.toast.preferenceConfirmError"));
    } finally {
      setPendingBusy(null);
    }
  }

  async function handleRejectPending(id: string) {
    setPendingBusy(id);
    try {
      await api.deleteMemory(id);
      setPending((prev) => prev.filter((m) => m.id !== id));
      toast(t("memory.toast.suggestionDismissed"));
      void refreshPendingCount();
    } catch {
      toast(t("memory.toast.suggestionDismissError"));
    } finally {
      setPendingBusy(null);
    }
  }

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
      toast(t("memory.toast.memoryDeleted"));
    } catch {
      toast(t("memory.toast.memoryDeleteError"));
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
      toast(t("memory.toast.pinError"));
    }
  }

  async function handleClearAll() {
    setConfirmClear(false);
    try {
      const { deleted_count } = await api.clearAllMemories();
      setMemories([]);
      toast(
        deleted_count === 0
          ? t("memory.clear.toastNone")
          : deleted_count === 1
            ? t("memory.clear.toastOne")
            : t("memory.clear.toastMany", { count: String(deleted_count) }),
      );
    } catch {
      toast(t("memory.clear.toastError"));
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      const res = await api.exportMemories();
      // Browser-side download: open a fresh tab pointed at the
      // download endpoint. The auth layer attaches the Portal
      // token via the same fetch chain elsewhere — for a direct
      // navigation we lean on the portal_access_token cookie that
      // the Portal mirrors cross-port on localhost (same trick
      // file downloads use today).
      window.open(api.exportDownloadUrl(res.export_id), "_blank");
      toast(t("memory.export.ready", { size: prettySize(res.size_bytes) }));
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : t("memory.export.error");
      toast(msg);
    } finally {
      setExporting(false);
    }
  }

  async function handlePermanentPurge() {
    setConfirmPurge(false);
    // Second factor: must type the literal phrase. window.prompt
    // is intentional — the backend enforces the same exact string
    // (handler.PurgeConfirmationPhrase) and we want both layers
    // to agree byte-for-byte rather than relying on UI state.
    const phrase = window.prompt(t("memory.danger.purgePromptBody"));
    if (phrase !== t("memory.danger.purgePromptPhrase")) {
      toast(t("memory.danger.purgeMismatch"));
      return;
    }
    setPurging(true);
    try {
      const res = await api.purgeMemories(phrase);
      // Wipe local state — the user expects a clean slate.
      setMemories([]);
      setPending([]);
      void refreshPendingCount();
      toast(
        res.memories_deleted === 1
          ? t("memory.danger.purgeDoneOne")
          : t("memory.danger.purgeDoneMany", {
              count: String(res.memories_deleted),
            }),
      );
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : t("memory.danger.purgeError");
      toast(msg);
    } finally {
      setPurging(false);
    }
  }

  function startTemporaryChat() {
    setNextConversationTemporary(true);
    useChatStore.getState().setActive(null);
    router.push("/chat");
    toast(t("memory.temporary.toast"));
  }

  // S9 — client-side filter. Lowercase contains across content +
  // category. Cheap at the per-user cap; falls through to the
  // full list when the search box is empty so the count + sort
  // stay correct.
  const filteredMemories = useMemo<Memory[] | null>(() => {
    if (!memories) return memories;
    const q = searchQuery.trim().toLowerCase();
    if (!q) return memories;
    return memories.filter((m) => {
      if (m.content && m.content.toLowerCase().includes(q)) return true;
      if (m.category && m.category.toLowerCase().includes(q)) return true;
      return false;
    });
  }, [memories, searchQuery]);

  const count = memories?.length ?? 0;
  const countLabel =
    count === 0
      ? t("memory.savedCount.zero")
      : count === 1
        ? t("memory.savedCount.one")
        : t("memory.savedCount.other", { count: String(count) });

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackArrow />
          <span>{t("memory.back")}</span>
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
              {t("memory.title")}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("memory.description")}
            </p>
          </div>
        </header>

        {/* S8 — Suggested Preferences. Only renders when the
            background extractor has surfaced patterns the user
            hasn't acted on yet. Sits ABOVE the saved-memories
            section so the action is the first thing the user
            sees on landing here from the sidebar badge. */}
        {pending.length > 0 ? (
          <section className="mt-8">
            <div className="flex items-baseline gap-2">
              <Sparkles className="h-3.5 w-3.5 text-text-tertiary" aria-hidden />
              <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted">
                {t("memory.suggested.title", { count: String(pending.length) })}
              </h2>
            </div>
            <p className="mt-1 text-xs text-text-tertiary">
              {t("memory.suggested.description")}
            </p>
            <div className="mt-3 space-y-2">
              {pending.map((m) => (
                <PendingPreferenceCard
                  key={m.id}
                  memory={m}
                  saving={pendingBusy === m.id}
                  onConfirm={(id) => handleConfirmPending(id)}
                  onReject={(id) => handleRejectPending(id)}
                />
              ))}
            </div>
            <div className="my-8 h-px bg-border-subtle" />
          </section>
        ) : null}

        <section className="mt-8">
          <div className="flex items-baseline justify-between">
            <h2 className="text-xs font-medium uppercase tracking-wider text-text-muted">
              {countLabel}
            </h2>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="focus-ring inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated"
            >
              <Plus className="h-3.5 w-3.5" />
              {t("memory.addMemory")}
            </button>
          </div>

          {/* S9 — client-side search input. Only renders when we
              have memories loaded; the input itself stays in DOM
              even when zero match the query so the user can
              clear or rephrase without losing focus. */}
          {memories && memories.length > 0 ? (
            <div className="relative mt-3">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-tertiary"
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("memory.searchPlaceholder")}
                className="w-full rounded-lg border border-border-subtle bg-bg-surface py-1.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
              />
            </div>
          ) : null}

          <div className="mt-3">
            {loading && memories === null ? (
              <LoadingSkeleton />
            ) : error ? (
              <ErrorState
                message={error}
                retryLabel={t("memory.error.tryAgain")}
                onRetry={() => void load()}
              />
            ) : !memories || memories.length === 0 ? (
              <EmptyState onAdd={() => setCreateOpen(true)} />
            ) : filteredMemories && filteredMemories.length === 0 ? (
              <p className="rounded-lg border border-border-subtle bg-bg-surface px-4 py-6 text-center text-xs text-text-tertiary">
                {t("memory.noResults", { query: searchQuery })}
              </p>
            ) : (
              <div className="space-y-2">
                {(filteredMemories ?? memories).map((m) => (
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

        {/* FIX22 §1c — the notes the desktop agent's `remember` tool saves on
            THIS computer (the sidecar's local store). They are folded into
            every desktop turn's prompt exactly like the memories above, but
            before FIX22 no screen listed them — a poisoned note could not be
            found or deleted. Hosted mode only (a browser has no local store). */}
        <LocalNotesSection />

        <Divider />

        <section>
          <h2 className="text-sm font-semibold text-text-primary">
            {t("memory.temporary.title")}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            {t("memory.temporary.description")}
          </p>
          <button
            type="button"
            onClick={startTemporaryChat}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated"
          >
            <Brain className="h-3.5 w-3.5" />
            {t("memory.temporary.button")}
          </button>
        </section>

        <Divider />

        {/* S9 — Data Management. Sits between Temporary chat and
            Danger zone so the user reads through the
            increasingly-destructive options in order. Export is
            non-destructive; clear-all is soft; permanent-delete
            is the final option in the Danger zone below. */}
        <section>
          <h2 className="text-sm font-semibold text-text-primary">
            {t("memory.export.title")}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            {t("memory.export.description")}
          </p>
          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={exporting || !memories || memories.length === 0}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-primary transition-colors hover:bg-bg-elevated disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download className="h-3.5 w-3.5" />
            {exporting ? t("memory.export.busy") : t("memory.export.button")}
          </button>
        </section>

        <Divider />

        <section>
          <h2 className="text-sm font-semibold text-status-danger">
            {t("memory.danger.title")}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            {t("memory.danger.clearDescription")}
          </p>
          <button
            type="button"
            onClick={() => setConfirmClear(true)}
            disabled={!memories || memories.length === 0}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-status-danger/40 bg-transparent px-3 py-1.5 text-xs text-status-danger transition-colors hover:bg-status-danger/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("memory.danger.clearButton")}
          </button>

          {/* S9 — permanent delete (GDPR right-to-erasure). HARD
              deletes memories + events + conversation states +
              source attributions + exports. Two-step confirm: the
              ConfirmDialog gates the action, and the next prompt
              forces the literal phrase the backend enforces. */}
          <p className="mt-6 text-sm text-text-secondary">
            <span className="font-semibold text-status-danger">
              {t("memory.danger.purgeDescriptionPrefix")}
            </span>{" "}
            {t("memory.danger.purgeDescription")}
          </p>
          <button
            type="button"
            onClick={() => setConfirmPurge(true)}
            disabled={purging}
            className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-lg border border-status-danger bg-status-danger/10 px-3 py-1.5 text-xs font-medium text-status-danger transition-colors hover:bg-status-danger/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            {purging ? t("memory.danger.purgeBusy") : t("memory.danger.purgeButton")}
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
        title={t("memory.confirm.clearTitle")}
        body={t("memory.confirm.clearBody")}
        cancelLabel={t("memory.confirm.cancel")}
        confirmLabel={t("memory.confirm.clearAction")}
        danger
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => void handleClearAll()}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("memory.confirm.deleteTitle")}
        body={pendingDelete ? `"${pendingDelete.content}"` : ""}
        cancelLabel={t("memory.confirm.cancel")}
        confirmLabel={t("memory.confirm.deleteAction")}
        danger
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
      <ConfirmDialog
        open={confirmPurge}
        title={t("memory.confirm.purgeTitle")}
        body={t("memory.confirm.purgeBody")}
        cancelLabel={t("memory.confirm.cancel")}
        confirmLabel={t("memory.confirm.purgeAction")}
        danger
        onCancel={() => setConfirmPurge(false)}
        onConfirm={() => void handlePermanentPurge()}
      />
    </div>
  );
}

// prettySize — formats the export size for the toast. KB/MB cap
// is enough for JSON memory dumps under a few MB.
function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
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

// EmptyState — communicates that memory is automatic. AI learns
// from natural conversation; the Add button is secondary.
function EmptyState({ onAdd }: { onAdd: () => void }) {
  const t = useT();
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-surface px-6 py-10 text-center">
      <Brain className="mx-auto h-9 w-9 text-text-tertiary" />
      <p className="mt-4 text-sm font-medium text-text-primary">
        {t("memory.empty.title")}
      </p>
      <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-text-secondary">
        {t("memory.empty.description")}
      </p>
      <p className="mx-auto mt-2 max-w-md text-xs italic text-text-muted">
        {t("memory.empty.hint")}
      </p>
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring mx-auto mt-5 inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-transparent px-3 py-1.5 text-xs text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <Plus className="h-3.5 w-3.5" />
        {t("memory.addMemory")}
      </button>
    </div>
  );
}

function ErrorState({
  message,
  retryLabel,
  onRetry,
}: {
  message: string;
  retryLabel: string;
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
        {retryLabel}
      </button>
    </div>
  );
}

// ── Confirm dialog (inlined — only the memory page uses it today) ──

function ConfirmDialog({
  open,
  title,
  body,
  cancelLabel,
  confirmLabel,
  danger,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  cancelLabel: string;
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
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`focus-ring rounded-lg px-6 py-2 text-sm font-medium transition-opacity hover:opacity-90 ${
              danger
                ? "bg-status-danger-solid text-white"
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
