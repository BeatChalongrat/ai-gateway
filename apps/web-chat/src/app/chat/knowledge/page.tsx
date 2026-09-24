"use client";

// Knowledge Base management page.
//
// Lists every KB the tenant has + lets the user create new ones,
// upload documents into them, see indexing status per document, and
// delete / reindex on demand. Citation-rendering for chat replies is
// owned by MessageBubble — this page is purely admin.
//
// Indexing is synchronous on the server (Phase 3 contract): the
// upload promise resolves with a row whose status is already
// "indexed" or "failed", so we don't poll here. A future async
// worker would justify adding a poll loop.
//
// FIX130 C2 — the page asks the server whether a knowledge base can work
// right now (GET /knowledge-bases/availability) every time it opens. When it
// cannot, the page says why in plain words and offers no create / upload —
// NIGHT2 F27 and the FIX130 test stack both saw every upload fail with the
// raw "embedding failed: gateway unavailable: status=503".

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import { useKnowledgeStore } from "@/stores/knowledgeStore";
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentStatus,
} from "@/lib/types";

const ALLOWED_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
  "text/plain",
].join(",");

export default function KnowledgeBasePage() {
  const t = useT();
  const knowledgeBases = useKnowledgeStore((s) => s.knowledgeBases);
  const loaded = useKnowledgeStore((s) => s.loaded);
  const loading = useKnowledgeStore((s) => s.loading);
  const loadKBs = useKnowledgeStore((s) => s.loadKnowledgeBases);
  const createKB = useKnowledgeStore((s) => s.createKnowledgeBase);
  const deleteKB = useKnowledgeStore((s) => s.deleteKnowledgeBase);

  const [createOpen, setCreateOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const availability = useKnowledgeStore((s) => s.availability);
  const availabilityReason = useKnowledgeStore((s) => s.availabilityReason);
  const loadAvailability = useKnowledgeStore((s) => s.loadAvailability);
  const [checking, setChecking] = useState(true);
  const recheck = () => {
    setChecking(true);
    void loadAvailability(true).finally(() => setChecking(false));
  };
  const canUse = availability === "available";

  useEffect(() => {
    if (!loaded && !loading) void loadKBs();
  }, [loaded, loading, loadKBs]);
  // Ask afresh on every visit — the verdict may have changed since the
  // session's first ask (a key was fixed, or credit ran out).
  useEffect(() => {
    void loadAvailability(true).finally(() => setChecking(false));
  }, [loadAvailability]);

  async function handleDeleteKB(kb: KnowledgeBase) {
    if (
      !window.confirm(
        tOutside("kb.confirmDelete", { name: kb.name, docs: String(kb.document_count), chunks: String(kb.total_chunks) }),
      )
    ) {
      return;
    }
    try {
      await deleteKB(kb.id);
      toast(tOutside("kb.toast.deleted"));
      if (expandedId === kb.id) setExpandedId(null);
    } catch {
      toast(tOutside("kb.toast.deleteFailed"));
    }
  }

  const hasKBs = loaded && knowledgeBases.length > 0;
  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        {/* Subtle back link — text-only, no button chrome. Matches
            the rest of the muted-link affordances elsewhere. */}
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
          {t("kb.back")}
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              {t("kb.title")}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("kb.subtitle")}
            </p>
          </div>
          {/* + Create only renders when there's at least one KB. The
              empty-state CTA handles the no-KBs case so we don't
              double up on calls-to-action. */}
          {hasKBs && canUse ? (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-transparent px-3.5 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
            >
              <PlusIcon />
              {t("kb.create")}
            </button>
          ) : null}
        </div>

        {!checking && !canUse ? (
          <KBUnavailableNotice reason={availabilityReason} onRecheck={recheck} />
        ) : null}

        <div className="mt-8">
          {!loaded || checking ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : knowledgeBases.length === 0 ? (
            canUse ? <EmptyKBState onCreate={() => setCreateOpen(true)} /> : null
          ) : (
            <div className="flex flex-col gap-3">
              {knowledgeBases.map((kb) => (
                <KBCard
                  key={kb.id}
                  kb={kb}
                  expanded={expandedId === kb.id}
                  onToggle={() =>
                    setExpandedId((id) => (id === kb.id ? null : kb.id))
                  }
                  onDelete={() => void handleDeleteKB(kb)}
                  canUpload={canUse}
                />
              ))}
            </div>
          )}
        </div>
      </main>

      <CreateKBModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={async (data) => {
          try {
            const kb = await createKB(data);
            toast(tOutside("kb.toast.created", { name: kb.name }));
            setCreateOpen(false);
            setExpandedId(kb.id);
          } catch (err) {
            toast(
              err instanceof Error
                ? err.message
                : "Couldn't create knowledge base.",
            );
          }
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FIX130 C2 — why a knowledge base cannot be used right now
// ---------------------------------------------------------------------------

// FIX132 — the knowledge base runs on local models (kb-models): the only
// reasons are that they are still loading or that they failed.
const UNAVAILABLE_REASON: Record<string, DictKey> = {
  models_loading: "kb.unavailable.models_loading",
  models_unavailable: "kb.unavailable.models_unavailable",
};

function KBUnavailableNotice({ reason, onRecheck }: { reason: string | null; onRecheck: () => void }) {
  const t = useT();
  return (
    <div role="status" data-testid="kb-unavailable" className="mt-6 rounded-xl border border-status-warning/40 bg-status-warning/10 px-5 py-4">
      <h2 className="text-sm font-semibold text-text-primary">{t("kb.unavailable.title")}</h2>
      <p className="mt-1 text-sm text-text-secondary">{t(UNAVAILABLE_REASON[reason ?? ""] ?? "kb.unavailable.unknown")}</p>
      <button
        type="button"
        onClick={onRecheck}
        className="focus-ring mt-3 rounded-md border border-border bg-transparent px-3 py-1.5 text-xs font-medium text-text-primary transition-colors hover:bg-bg-elevated"
      >
        {t("kb.unavailable.recheck")}
      </button>
    </div>
  );
}

/** A failed document's reason in the person's language: an embeddings
 *  failure (the only one the live system produces) has its own sentence;
 *  anything else keeps the server's text. */
function docFailureText(message: string | null | undefined): string {
  if (!message || /embedding/i.test(message)) return tOutside("kb.doc.embeddingFailed");
  return message;
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyKBState({ onCreate }: { onCreate: () => void }) {
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
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <path d="M14 3v6h6" />
        <path d="M8 13h8M8 17h6" />
      </svg>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        {t("kb.empty.title")}
      </h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-text-secondary">
        {t("kb.empty.body")}
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="focus-ring mt-6 inline-flex items-center gap-1.5 rounded-lg border border-border bg-transparent px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
      >
        <PlusIcon />
        {t("kb.create.full")}
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
// KB row + expanded panel
// ---------------------------------------------------------------------------

function KBCard({
  kb,
  expanded,
  onToggle,
  onDelete,
  canUpload,
}: {
  kb: KnowledgeBase;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
  canUpload: boolean;
}) {
  const t = useT();
  // Status label — only one of indexed/processing/failed/inactive
  // shows, chosen by the most-relevant signal. Active + all docs
  // indexed → "Indexed" (positive). Anything else falls through.
  const statusLabel = !kb.is_active
    ? t("kb.status.inactive")
    : kb.document_count === 0
      ? t("kb.status.empty")
      : t("kb.status.indexed");

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
            {kb.name}
          </h3>
          {kb.description ? (
            <p className="mt-1 truncate text-sm text-text-secondary">
              {kb.description}
            </p>
          ) : null}
          <p className="mt-3 text-xs text-text-muted">
            {kb.document_count}{" "}
            {kb.document_count === 1
              ? t("kb.unit.document")
              : t("kb.unit.documents")}
            {" · "}
            {kb.total_chunks}{" "}
            {kb.total_chunks === 1 ? t("kb.unit.chunk") : t("kb.unit.chunks")}
            {" · "}
            {statusLabel}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 text-sm text-text-secondary transition-colors group-hover:text-text-primary">
          <span>{expanded ? t("kb.card.close") : t("kb.card.manage")}</span>
          {expanded ? <ChevronIcon up /> : <ArrowRightIcon />}
        </span>
      </button>
      {expanded ? <KBDocumentsPanel kb={kb} onDelete={onDelete} canUpload={canUpload} /> : null}
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

function KBDocumentsPanel({
  kb,
  onDelete,
  canUpload,
}: {
  kb: KnowledgeBase;
  onDelete: () => void;
  canUpload: boolean;
}) {
  const t = useT();
  const documents = useKnowledgeStore((s) => s.documentsByKB[kb.id]);
  const uploading = useKnowledgeStore((s) => s.uploadingByKB[kb.id]) ?? [];
  const loadDocs = useKnowledgeStore((s) => s.loadDocuments);
  const uploadDocument = useKnowledgeStore((s) => s.uploadDocument);
  const deleteDocument = useKnowledgeStore((s) => s.deleteDocument);
  const reindexDocument = useKnowledgeStore((s) => s.reindexDocument);
  const loadAvailability = useKnowledgeStore((s) => s.loadAvailability);

  useEffect(() => {
    if (documents === undefined) void loadDocs(kb.id);
  }, [kb.id, documents, loadDocs]);

  async function handleUpload(files: FileList) {
    for (let i = 0; i < files.length; i++) {
      const file = files.item(i);
      if (!file) continue;
      try {
        const doc = await uploadDocument(kb.id, file);
        if (doc.status === "failed") {
          // FIX130 C2 — said in words (the row keeps it too), and the page
          // re-asks whether knowledge bases work at all: the server has just
          // learned they do not.
          toast(tOutside("kb.toast.docFailed", { name: doc.filename, error: docFailureText(doc.error_message) }));
          void loadAvailability(true);
        } else {
          toast(tOutside("kb.toast.docIndexed", { name: doc.filename, n: String(doc.chunk_count) }));
        }
      } catch (err) {
        toast(
          err instanceof Error ? err.message : "Upload failed.",
        );
      }
    }
  }

  async function handleDeleteDoc(doc: KnowledgeDocument) {
    if (!window.confirm(t("kb.confirmDeleteDoc", { name: doc.filename }))) return;
    try {
      await deleteDocument(kb.id, doc.id);
      toast(tOutside("kb.toast.docDeleted"));
    } catch {
      toast(tOutside("kb.toast.docDeleteFailed"));
    }
  }

  async function handleReindex(doc: KnowledgeDocument) {
    try {
      await reindexDocument(kb.id, doc.id);
      toast(tOutside("kb.toast.reindexing", { name: doc.filename }));
    } catch {
      toast(tOutside("kb.toast.reindexFailed"));
    }
  }

  return (
    <div className="border-t border-border bg-bg-base/50 px-4 py-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-secondary">
          {t("kb.docs.title")}
        </h3>
        <div className="flex items-center gap-2">
          {canUpload ? (
            <label className="cursor-pointer">
              <input
                type="file"
                multiple
                accept={ALLOWED_MIME}
                className="sr-only"
                onChange={(e) => {
                  if (e.target.files) void handleUpload(e.target.files);
                  e.target.value = "";
                }}
              />
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-primary hover:bg-bg-elevated">
                <PlusIcon /> {t("kb.docs.upload")}
              </span>
            </label>
          ) : null}
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md px-2 py-1.5 text-xs text-status-danger hover:bg-status-danger/10"
          >
            {t("kb.docs.deleteKb")}
          </button>
        </div>
      </div>

      {documents === undefined ? (
        <div className="flex justify-center py-4">
          <Spinner size={16} />
        </div>
      ) : documents.length === 0 && uploading.length === 0 ? (
        <p className="py-2 text-xs text-text-muted">{t("kb.docs.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {uploading.map((u) => (
            <li
              key={u.tempId}
              className="flex items-center gap-3 rounded-md border border-border bg-bg-surface px-3 py-2 text-xs"
            >
              <Spinner size={12} />
              <span className="min-w-0 flex-1 truncate text-text-primary">
                {u.filename}
              </span>
              <span className="text-text-muted">{u.progress}%</span>
            </li>
          ))}
          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              onDelete={() => void handleDeleteDoc(doc)}
              onReindex={() => void handleReindex(doc)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function DocumentRow({
  doc,
  onDelete,
  onReindex,
}: {
  doc: KnowledgeDocument;
  onDelete: () => void;
  onReindex: () => void;
}) {
  const t = useT();
  return (
    <li className="flex items-center gap-3 rounded-md border border-border bg-bg-surface px-3 py-2">
      <StatusPill status={doc.status} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-text-primary">
          {doc.filename}
        </p>
        <p className="text-[11px] text-text-muted">
          {humanSize(doc.file_size_bytes)}
          {doc.status === "indexed"
            ? ` · ${doc.chunk_count} ${doc.chunk_count === 1 ? t("kb.unit.chunk") : t("kb.unit.chunks")}`
            : ""}
          {doc.status === "failed" ? ` · ${docFailureText(doc.error_message)}` : ""}
        </p>
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onReindex}
          title="Reindex"
          className="tap-44 rounded p-1.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
        >
          <RefreshIcon />
        </button>
        <button
          type="button"
          onClick={onDelete}
          title="Delete"
          className="tap-44 rounded p-1.5 text-text-muted hover:bg-status-danger/10 hover:text-status-danger"
        >
          <TrashIcon />
        </button>
      </div>
    </li>
  );
}

function StatusPill({ status }: { status: KnowledgeDocumentStatus }) {
  const t = useT();
  const map: Record<
    KnowledgeDocumentStatus,
    { label: string; color: string; dot: string }
  > = {
    pending: {
      label: t("kb.doc.status.pending"),
      color: "text-text-muted border-border",
      dot: "bg-text-muted",
    },
    processing: {
      label: t("kb.doc.status.processing"),
      color: "text-status-warning border-status-warning/40",
      dot: "bg-status-warning",
    },
    indexed: {
      label: t("kb.doc.status.indexed"),
      color: "text-status-success border-status-success/40",
      dot: "bg-status-success",
    },
    failed: {
      label: t("kb.doc.status.failed"),
      color: "text-status-danger border-status-danger/40",
      dot: "bg-status-danger",
    },
  };
  const s = map[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border bg-bg-base px-2 py-0.5 text-[10px] uppercase tracking-wider ${s.color}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden />
      {s.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Create-KB modal
// ---------------------------------------------------------------------------

function CreateKBModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (data: {
    name: string;
    description: string;
    chunk_size: number;
    chunk_overlap: number;
  }) => Promise<void>;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [chunkSize, setChunkSize] = useState(512);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      setName("");
      setDescription("");
      setAdvanced(false);
      setChunkSize(512);
      setChunkOverlap(50);
      setSubmitting(false);
    }
  }, [open]);

  async function submit() {
    if (!name.trim()) return;
    setSubmitting(true);
    try {
      await onCreate({
        name: name.trim(),
        description: description.trim(),
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("kb.dialog.title")}
      description={t("kb.dialog.description")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={() => void submit()} loading={submitting}>
            {t("kb.create")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label={t("kb.dialog.name")}
          placeholder={t("kb.dialog.namePlaceholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <Input
          label={t("kb.dialog.desc")}
          placeholder={t("kb.dialog.descPlaceholder")}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          className="self-start text-xs text-text-muted hover:text-text-primary"
        >
          {advanced ? t("kb.dialog.hideAdvanced") : t("kb.dialog.showAdvanced")}
        </button>
        {advanced ? (
          <div className="grid grid-cols-2 gap-3">
            <Input
              label={t("kb.dialog.chunkSize")}
              type="number"
              min={100}
              max={2048}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value) || 512)}
            />
            <Input
              label={t("kb.dialog.chunkOverlap")}
              type="number"
              min={0}
              max={512}
              value={chunkOverlap}
              onChange={(e) => setChunkOverlap(Number(e.target.value) || 0)}
            />
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Icons + helpers — PlusIcon lives near the empty state above; the
// rest are shared between the documents panel rows.
// ---------------------------------------------------------------------------

function TrashIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function humanSize(n: number): string {
  const KB = 1024;
  const MB = KB * 1024;
  if (n >= MB) return `${(n / MB).toFixed(1)} MB`;
  if (n >= KB) return `${Math.round(n / KB)} KB`;
  return `${n} B`;
}
