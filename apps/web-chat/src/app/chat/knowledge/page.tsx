"use client";

// Knowledge Base management page.
//
// Lists the person's KBs + lets them create new ones, upload documents into
// them, see indexing status per document, and delete / retry on demand.
// Citation-rendering for chat replies is owned by MessageBubble
// (KnowledgeSources) — this page manages the documents.
//
// FIX132 — indexing is asynchronous: an upload answers 202 with a `pending`
// row and the server processes it in the background. The open KB's documents
// are therefore re-read every ~2 s while any of them is pending/processing
// (and on window focus), each row says where it is — รอคิว, กำลังประมวลผล
// 12/40 ส่วน with a bar, พร้อมใช้ with its chunk count, or ไม่สำเร็จ with the
// reason for its error_code and ลองใหม่ — and while anything is still being
// processed a note says the person can already ask: those files are just not
// used for answers yet. A file that never became a document (refused by the
// page's own type/size check, or a failed request) stays on its row with the
// reason until it is retried or removed; no failure is only a toast.
//
// FIX130 C2 — the page asks the server whether a knowledge base can work
// right now (GET /knowledge-bases/availability) every time it opens. When it
// cannot, the page says why in plain words and offers no create / upload —
// NIGHT2 F27 and the FIX130 test stack both saw every upload fail with the
// raw "embedding failed: gateway unavailable: status=503".

import { useEffect, useState } from "react";
import Link from "next/link";
import { CircleAlert, FileText, Upload } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import {
  docErrorKey,
  isDocInFlight,
  KB_ACCEPT,
  KB_POLL,
  uploadErrorKey,
  uploadRetryable,
} from "@/lib/knowledge";
import { useKnowledgeStore, type UploadingDocument } from "@/stores/knowledgeStore";
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentStatus,
} from "@/lib/types";

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
  // FIX132 — a closed KB's card says "กำลังประมวลผล N ไฟล์" from the list;
  // while any says so, re-read the list (the open KB re-reads its own rows).
  const listIndexing = knowledgeBases.some(
    (k) => k.id !== expandedId && (k.indexing?.pending ?? 0) + (k.indexing?.processing ?? 0) > 0,
  );
  useEffect(() => {
    if (!listIndexing) return;
    const id = window.setInterval(() => void loadKBs(), KB_POLL.listMs);
    return () => window.clearInterval(id);
  }, [listIndexing, loadKBs]);
  // Ask afresh on every visit — the verdict may have changed since the
  // session's first ask (a key was fixed, or credit ran out).
  useEffect(() => {
    void loadAvailability(true).finally(() => setChecking(false));
  }, [loadAvailability]);

  // FIX132 — confirmed in the app (window.confirm is a bare system box in
  // the desktop app); a refusal is said inside the dialog.
  const [confirmKB, setConfirmKB] = useState<KnowledgeBase | null>(null);
  async function handleDeleteKB(kb: KnowledgeBase) {
    await deleteKB(kb.id);
    toast(tOutside("kb.toast.deleted"));
    if (expandedId === kb.id) setExpandedId(null);
    setConfirmKB(null);
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
                  onDelete={() => setConfirmKB(kb)}
                  canUpload={canUse}
                />
              ))}
            </div>
          )}
        </div>
      </main>

      <ConfirmDialog
        open={confirmKB !== null}
        title={t("kb.confirmKb.title")}
        body={
          confirmKB
            ? t("kb.confirmDelete", {
                name: confirmKB.name,
                docs: String(confirmKB.document_count),
                chunks: String(confirmKB.total_chunks),
              })
            : ""
        }
        actionLabel={t("kb.confirmKb.action")}
        errorLabel={t("kb.toast.deleteFailed")}
        onCancel={() => setConfirmKB(null)}
        onConfirm={() => (confirmKB ? handleDeleteKB(confirmKB) : Promise.resolve())}
      />

      <CreateKBModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={async (data) => {
          // A refusal throws into the dialog, which says so and stays open.
          const kb = await createKB(data);
          toast(tOutside("kb.toast.created", { name: kb.name }));
          setCreateOpen(false);
          setExpandedId(kb.id);
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
  const inFlight = (kb.indexing?.pending ?? 0) + (kb.indexing?.processing ?? 0);
  const failed = kb.indexing?.failed ?? 0;

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
          <p data-testid="kb-card-summary" className="mt-3 text-xs text-text-muted">
            {kb.document_count}{" "}
            {kb.document_count === 1
              ? t("kb.unit.document")
              : t("kb.unit.documents")}
            {" · "}
            {kb.total_chunks}{" "}
            {kb.total_chunks === 1 ? t("kb.unit.chunk") : t("kb.unit.chunks")}
            {" · "}
            {inFlight > 0 || failed > 0 ? (
              <>
                {inFlight > 0 ? (
                  <span className="text-status-warning">{t("kb.card.indexing", { n: String(inFlight) })}</span>
                ) : null}
                {inFlight > 0 && failed > 0 ? " · " : null}
                {failed > 0 ? (
                  <span className="text-status-danger">{t("kb.card.failed", { n: String(failed) })}</span>
                ) : null}
              </>
            ) : (
              statusLabel
            )}
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
  const uploads = useKnowledgeStore((s) => s.uploadingByKB[kb.id]) ?? NO_UPLOADS;
  const loadDocs = useKnowledgeStore((s) => s.loadDocuments);
  const uploadFiles = useKnowledgeStore((s) => s.uploadFiles);
  const retryUpload = useKnowledgeStore((s) => s.retryUpload);
  const dismissUpload = useKnowledgeStore((s) => s.dismissUpload);
  const deleteDocument = useKnowledgeStore((s) => s.deleteDocument);
  const reindexDocument = useKnowledgeStore((s) => s.reindexDocument);
  const [dragging, setDragging] = useState(false);

  const inFlight = (documents ?? []).some(isDocInFlight);

  // Fresh on every open.
  useEffect(() => {
    void loadDocs(kb.id);
  }, [kb.id, loadDocs]);
  // Re-read while something is queued or processing; stop when nothing is.
  useEffect(() => {
    if (!inFlight) return;
    const id = window.setInterval(() => void loadDocs(kb.id), KB_POLL.docsMs);
    return () => window.clearInterval(id);
  }, [inFlight, kb.id, loadDocs]);
  // Coming back to the page (another tab, the desktop window) re-reads too.
  useEffect(() => {
    const onFocus = () => void loadDocs(kb.id);
    const onVisible = () => {
      if (document.visibilityState === "visible") onFocus();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [kb.id, loadDocs]);

  function send(files: File[]) {
    if (files.length > 0) void uploadFiles(kb.id, files);
  }

  // FIX132 — the confirmation says what deleting means for answers.
  const [confirmDoc, setConfirmDoc] = useState<KnowledgeDocument | null>(null);
  async function handleDeleteDoc(doc: KnowledgeDocument) {
    await deleteDocument(kb.id, doc.id);
    toast(tOutside("kb.toast.docDeleted"));
    setConfirmDoc(null);
  }

  return (
    <div
      className="border-t border-border bg-bg-base/50 px-4 py-4"
      onDragOver={(e) => {
        if (!canUpload) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setDragging(false);
      }}
      onDrop={(e) => {
        if (!canUpload) return;
        e.preventDefault();
        setDragging(false);
        send(Array.from(e.dataTransfer?.files ?? []));
      }}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-text-secondary">
          {t("kb.docs.title")}
        </h3>
        <button
          type="button"
          onClick={onDelete}
          className="rounded-md px-2 py-1.5 text-xs text-status-danger hover:bg-status-danger/10"
        >
          {t("kb.docs.deleteKb")}
        </button>
      </div>

      {canUpload ? (
        <div
          data-testid="kb-dropzone"
          className={`mb-3 flex flex-col items-center gap-1 rounded-lg border border-dashed px-4 py-5 text-center transition-colors ${
            dragging ? "border-border-strong bg-bg-elevated" : "border-border bg-transparent"
          }`}
        >
          <Upload className="h-4 w-4 text-text-muted" aria-hidden />
          <p className="text-xs text-text-secondary">
            {t("kb.drop.title")}{" "}
            <label className="cursor-pointer font-medium text-text-primary underline-offset-2 hover:underline">
              <input
                type="file"
                multiple
                accept={KB_ACCEPT}
                className="sr-only"
                data-testid="kb-file-input"
                onChange={(e) => {
                  send(Array.from(e.target.files ?? []));
                  e.target.value = "";
                }}
              />
              {t("kb.drop.pick")}
            </label>
          </p>
          <p className="text-[11px] text-text-muted">{t("kb.drop.hint")}</p>
        </div>
      ) : null}

      {inFlight ? (
        <p
          role="status"
          data-testid="kb-processing-note"
          className="mb-3 rounded-md border border-border bg-bg-surface px-3 py-2 text-xs text-text-secondary"
        >
          {t("kb.processingNote")}
        </p>
      ) : null}

      {documents === undefined ? (
        <div className="flex justify-center py-4">
          <Spinner size={16} />
        </div>
      ) : documents.length === 0 && uploads.length === 0 ? (
        <p className="py-2 text-xs text-text-muted">{t("kb.docs.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {uploads.map((u) => (
            <UploadRow
              key={u.tempId}
              upload={u}
              onRetry={() => void retryUpload(kb.id, u.tempId)}
              onDismiss={() => dismissUpload(kb.id, u.tempId)}
            />
          ))}
          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              onDelete={() => setConfirmDoc(doc)}
              onRetry={() => reindexDocument(kb.id, doc.id)}
            />
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={confirmDoc !== null}
        title={t("kb.confirmDoc.title")}
        body={confirmDoc ? t("kb.confirmDoc.body", { name: confirmDoc.filename }) : ""}
        actionLabel={t("kb.confirmDoc.action")}
        errorLabel={t("kb.toast.docDeleteFailed")}
        onCancel={() => setConfirmDoc(null)}
        onConfirm={() => (confirmDoc ? handleDeleteDoc(confirmDoc) : Promise.resolve())}
      />
    </div>
  );
}

/** A destructive action confirmed inside the app. `onConfirm` throws when
 *  the server refused; the dialog then stays open and says so. */
function ConfirmDialog({
  open,
  title,
  body,
  actionLabel,
  errorLabel,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  actionLabel: string;
  errorLabel: string;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!open) {
      setBusy(false);
      setFailed(false);
    }
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="danger"
            loading={busy}
            onClick={() => {
              setBusy(true);
              setFailed(false);
              onConfirm()
                .catch(() => setFailed(true))
                .finally(() => setBusy(false));
            }}
          >
            {actionLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm text-text-secondary">{body}</p>
      {failed ? (
        <p role="alert" className="mt-3 text-xs text-status-danger">
          {errorLabel}
        </p>
      ) : null}
    </Modal>
  );
}

const NO_UPLOADS: UploadingDocument[] = [];

const ROW = "flex items-start gap-3 rounded-md border border-border bg-bg-surface px-3 py-2";
const ROW_BUTTON =
  "focus-ring rounded-md border border-border bg-transparent px-2 py-1 text-[11px] font-medium text-text-primary transition-colors hover:bg-bg-elevated disabled:opacity-60";

/** A chosen file that is not a document (yet): its bytes are going up, or it
 *  did not get there — then the reason stays here until retried or removed. */
function UploadRow({
  upload,
  onRetry,
  onDismiss,
}: {
  upload: UploadingDocument;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const t = useT();
  const failed = upload.state === "failed";
  return (
    <li data-testid="kb-upload-row" data-state={upload.state} className={ROW}>
      <span className="mt-0.5 shrink-0">
        {failed ? <CircleAlert className="h-3.5 w-3.5 text-status-danger" aria-hidden /> : <Spinner size={12} />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-text-primary">{upload.filename}</p>
        {failed && upload.errorCode ? (
          <p role="alert" className="mt-0.5 text-[11px] text-status-danger">
            {t(uploadErrorKey(upload.errorCode))}
          </p>
        ) : (
          <p className="mt-0.5 text-[11px] text-text-muted">
            {t("kb.upload.sending", { pct: String(upload.progress) })}
          </p>
        )}
      </div>
      {failed ? (
        <div className="flex shrink-0 items-center gap-1">
          {upload.errorCode && uploadRetryable(upload.errorCode) ? (
            <button type="button" onClick={onRetry} className={ROW_BUTTON}>
              {t("kb.doc.retry")}
            </button>
          ) : null}
          <button type="button" onClick={onDismiss} className={ROW_BUTTON}>
            {t("kb.upload.dismiss")}
          </button>
        </div>
      ) : null}
    </li>
  );
}

function statusText(t: ReturnType<typeof useT>, doc: KnowledgeDocument): string {
  switch (doc.status) {
    case "pending":
      return t("kb.doc.status.pending");
    case "processing":
      return (doc.progress_total ?? 0) > 0
        ? t("kb.doc.progress", { done: String(doc.progress_done ?? 0), total: String(doc.progress_total) })
        : t("kb.doc.status.processing");
    case "indexed":
      return t("kb.doc.ready", { n: String(doc.chunk_count) });
    default:
      return t("kb.doc.status.failed");
  }
}

const STATUS_TONE: Record<KnowledgeDocumentStatus, string> = {
  pending: "text-text-muted",
  processing: "text-status-warning",
  indexed: "text-status-success",
  failed: "text-status-danger",
};

function DocumentRow({
  doc,
  onDelete,
  onRetry,
}: {
  doc: KnowledgeDocument;
  onDelete: () => void;
  onRetry: () => Promise<void>;
}) {
  const t = useT();
  const [actionError, setActionError] = useState<DictKey | null>(null);
  const failed = doc.status === "failed";
  const total = doc.progress_total ?? 0;
  const done = Math.min(doc.progress_done ?? 0, total);
  return (
    <li data-testid="kb-doc-row" data-status={doc.status} className={ROW}>
      <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-muted" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-text-primary">{doc.filename}</p>
        <p className="mt-0.5 text-[11px] text-text-muted">
          <span data-testid="kb-doc-status" className={STATUS_TONE[doc.status]}>
            {statusText(t, doc)}
          </span>
          {" · "}
          {humanSize(doc.file_size_bytes)}
        </p>
        {doc.status === "processing" && total > 0 ? (
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            aria-label={doc.filename}
            className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-bg-elevated"
          >
            <div
              className="h-full rounded-full bg-status-warning transition-[width] duration-500"
              style={{ width: `${Math.round((done / total) * 100)}%` }}
            />
          </div>
        ) : null}
        {failed ? (
          <p data-testid="kb-doc-reason" className="mt-0.5 text-[11px] text-status-danger">
            {t(docErrorKey(doc.error_code))}
          </p>
        ) : null}
        {actionError ? (
          <p role="alert" className="mt-0.5 text-[11px] text-status-danger">
            {t(actionError)}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {failed ? (
          <button
            type="button"
            onClick={() => {
              setActionError(null);
              onRetry().catch(() => setActionError("kb.doc.retryFailed"));
            }}
            className={ROW_BUTTON}
          >
            {t("kb.doc.retry")}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onDelete}
          aria-label={t("kb.doc.delete")}
          title={t("kb.doc.delete")}
          className="tap-44 rounded p-1.5 text-text-muted hover:bg-status-danger/10 hover:text-status-danger"
        >
          <TrashIcon />
        </button>
      </div>
    </li>
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
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open) {
      setName("");
      setDescription("");
      setAdvanced(false);
      setChunkSize(512);
      setChunkOverlap(50);
      setSubmitting(false);
      setFailed(false);
    }
  }, [open]);

  async function submit() {
    if (!name.trim()) return;
    setSubmitting(true);
    setFailed(false);
    try {
      await onCreate({
        name: name.trim(),
        description: description.trim(),
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
      });
    } catch {
      setFailed(true);
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
        {failed ? (
          <p role="alert" className="text-xs text-status-danger">
            {t("kb.dialog.createFailed")}
          </p>
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

function humanSize(n: number): string {
  const KB = 1024;
  const MB = KB * 1024;
  if (n >= MB) return `${(n / MB).toFixed(1)} MB`;
  if (n >= KB) return `${Math.round(n / KB)} KB`;
  return `${n} B`;
}
