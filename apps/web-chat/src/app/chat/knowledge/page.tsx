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

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
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
  const knowledgeBases = useKnowledgeStore((s) => s.knowledgeBases);
  const loaded = useKnowledgeStore((s) => s.loaded);
  const loading = useKnowledgeStore((s) => s.loading);
  const loadKBs = useKnowledgeStore((s) => s.loadKnowledgeBases);
  const createKB = useKnowledgeStore((s) => s.createKnowledgeBase);
  const deleteKB = useKnowledgeStore((s) => s.deleteKnowledgeBase);

  const [createOpen, setCreateOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (!loaded && !loading) void loadKBs();
  }, [loaded, loading, loadKBs]);

  async function handleDeleteKB(kb: KnowledgeBase) {
    if (
      !window.confirm(
        `Delete "${kb.name}"? All ${kb.document_count} documents and ${kb.total_chunks} chunks will be permanently removed.`,
      )
    ) {
      return;
    }
    try {
      await deleteKB(kb.id);
      toast("Knowledge base deleted");
      if (expandedId === kb.id) setExpandedId(null);
    } catch {
      toast("Couldn't delete knowledge base.");
    }
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <header className="border-b border-border/60 px-6 py-5">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4">
          <div>
            {/* Back affordance — explicit arrow + label is clearer
                than a passive breadcrumb. Tap target hits min 32px. */}
            <Link
              href="/chat"
              className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wider text-text-muted transition-colors hover:text-text-primary"
            >
              <svg
                width={12}
                height={12}
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
              Back to chat
            </Link>
            <h1 className="mt-2 text-xl font-semibold tracking-tight text-text-primary">
              Knowledge bases
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              Upload documents that the AI can retrieve from when answering
              questions. Bind a knowledge base to a conversation from the
              chat input.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>+ Create</Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-6">
        {!loaded ? (
          <div className="flex items-center justify-center py-10 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : knowledgeBases.length === 0 ? (
          <EmptyKBState onCreate={() => setCreateOpen(true)} />
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
              />
            ))}
          </div>
        )}
      </main>

      <CreateKBModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={async (data) => {
          try {
            const kb = await createKB(data);
            toast(`Created "${kb.name}"`);
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
// Empty state
// ---------------------------------------------------------------------------

function EmptyKBState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-bg-surface/40 p-10 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-bg-elevated">
        <svg
          width={22}
          height={22}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
          className="text-text-muted"
        >
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
      </div>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">
        No knowledge bases yet
      </h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-text-secondary">
        Create a knowledge base to give the AI context from your own
        documents — internal policies, handbooks, FAQs, anything.
      </p>
      <Button className="mt-5" onClick={onCreate}>
        + Create knowledge base
      </Button>
    </div>
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
}: {
  kb: KnowledgeBase;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-bg-surface">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-bg-elevated"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-text-primary">
              {kb.name}
            </span>
            {!kb.is_active ? (
              <span className="rounded-full bg-bg-elevated px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-text-muted">
                inactive
              </span>
            ) : null}
          </div>
          {kb.description ? (
            <p className="mt-0.5 truncate text-xs text-text-muted">
              {kb.description}
            </p>
          ) : null}
          <p className="mt-1 text-[11px] text-text-muted">
            {kb.document_count} {kb.document_count === 1 ? "doc" : "docs"} ·{" "}
            {kb.total_chunks} {kb.total_chunks === 1 ? "chunk" : "chunks"}
          </p>
        </div>
        <ChevronIcon up={expanded} />
      </button>
      {expanded ? <KBDocumentsPanel kb={kb} onDelete={onDelete} /> : null}
    </section>
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
}: {
  kb: KnowledgeBase;
  onDelete: () => void;
}) {
  const documents = useKnowledgeStore((s) => s.documentsByKB[kb.id]);
  const uploading = useKnowledgeStore((s) => s.uploadingByKB[kb.id]) ?? [];
  const loadDocs = useKnowledgeStore((s) => s.loadDocuments);
  const uploadDocument = useKnowledgeStore((s) => s.uploadDocument);
  const deleteDocument = useKnowledgeStore((s) => s.deleteDocument);
  const reindexDocument = useKnowledgeStore((s) => s.reindexDocument);

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
          toast(`"${doc.filename}" failed: ${doc.error_message ?? "unknown"}`);
        } else {
          toast(`Indexed "${doc.filename}" (${doc.chunk_count} chunks)`);
        }
      } catch (err) {
        toast(
          err instanceof Error ? err.message : "Upload failed.",
        );
      }
    }
  }

  async function handleDeleteDoc(doc: KnowledgeDocument) {
    if (!window.confirm(`Delete "${doc.filename}"?`)) return;
    try {
      await deleteDocument(kb.id, doc.id);
      toast("Document deleted");
    } catch {
      toast("Couldn't delete document.");
    }
  }

  async function handleReindex(doc: KnowledgeDocument) {
    try {
      await reindexDocument(kb.id, doc.id);
      toast(`Reindexing "${doc.filename}"…`);
    } catch {
      toast("Couldn't reindex document.");
    }
  }

  return (
    <div className="border-t border-border/60 bg-bg-base/50 px-4 py-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-secondary">
          Documents
        </h3>
        <div className="flex items-center gap-2">
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
              <PlusIcon /> Upload
            </span>
          </label>
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md px-2 py-1.5 text-xs text-status-danger hover:bg-status-danger/10"
          >
            Delete KB
          </button>
        </div>
      </div>

      {documents === undefined ? (
        <div className="flex justify-center py-4">
          <Spinner size={16} />
        </div>
      ) : documents.length === 0 && uploading.length === 0 ? (
        <p className="py-2 text-xs text-text-muted">
          No documents yet. Upload PDFs, DOCX, XLSX, CSV, or TXT — up to 50 MB
          each.
        </p>
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
            ? ` · ${doc.chunk_count} ${doc.chunk_count === 1 ? "chunk" : "chunks"}`
            : ""}
          {doc.status === "failed" && doc.error_message
            ? ` · ${doc.error_message}`
            : ""}
        </p>
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onReindex}
          title="Reindex"
          className="rounded p-1.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
        >
          <RefreshIcon />
        </button>
        <button
          type="button"
          onClick={onDelete}
          title="Delete"
          className="rounded p-1.5 text-text-muted hover:bg-status-danger/10 hover:text-status-danger"
        >
          <TrashIcon />
        </button>
      </div>
    </li>
  );
}

function StatusPill({ status }: { status: KnowledgeDocumentStatus }) {
  const map: Record<
    KnowledgeDocumentStatus,
    { label: string; color: string; dot: string }
  > = {
    pending: {
      label: "pending",
      color: "text-text-muted border-border",
      dot: "bg-text-muted",
    },
    processing: {
      label: "indexing",
      color: "text-status-warning border-status-warning/40",
      dot: "bg-status-warning",
    },
    indexed: {
      label: "indexed",
      color: "text-status-success border-status-success/40",
      dot: "bg-status-success",
    },
    failed: {
      label: "failed",
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
      title="New knowledge base"
      description="Group of documents the AI can retrieve from"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={submitting}>
            Create
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input
          label="Name"
          placeholder="Company handbook"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <Input
          label="Description (optional)"
          placeholder="Internal policies, onboarding docs…"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          className="self-start text-xs text-text-muted hover:text-text-primary"
        >
          {advanced ? "Hide" : "Show"} advanced options
        </button>
        {advanced ? (
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Chunk size"
              type="number"
              min={100}
              max={2048}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value) || 512)}
            />
            <Input
              label="Chunk overlap"
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
// Icons + helpers
// ---------------------------------------------------------------------------

function PlusIcon() {
  return (
    <svg
      width={12}
      height={12}
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
