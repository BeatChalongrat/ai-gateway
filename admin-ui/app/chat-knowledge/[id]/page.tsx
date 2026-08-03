"use client";

// Admin KB detail — header + stats + documents table + sample chunks
// peek + danger zone (reindex, delete).
//
// The reindex call is synchronous server-side and can take minutes
// for big KBs; the UI shows a spinning button while it runs and
// re-loads on completion. Reindex and delete each confirm via the
// shared ConfirmModal.

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import PageHeader from "../../../components/ui/PageHeader";
import ErrorState from "../../../components/ui/ErrorState";
import ConfirmModal from "../../../components/ui/ConfirmModal";
import StatCard from "../../../components/ui/StatCard";
import { SkeletonTable } from "../../../components/ui/Skeleton";
import { useT, type DictKey } from "../../../lib/i18n";
import {
  AdminKBDetailResponse,
  AdminKBDocRow,
  deleteAdminKnowledgeBase,
  fetchAdminKnowledgeBase,
  reindexAdminKnowledgeBase,
} from "../../../lib/chatEdgeApi";

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function humanSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

type Translate = ReturnType<typeof useT>;

export default function ChatKnowledgeDetailPage() {
  const t = useT();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = typeof params?.id === "string" ? params.id : "";

  const [data, setData] = useState<AdminKBDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [reindexConfirmOpen, setReindexConfirmOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAdminKnowledgeBase(id);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("state.errorLoad"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function doReindex() {
    if (!data) return;
    setReindexing(true);
    setActionMessage(null);
    try {
      const res = await reindexAdminKnowledgeBase(id);
      setActionMessage(
        t("chat.kb.reindexResult", { done: res.reindexed, total: res.total }) +
          (res.failed > 0
            ? ` ${t("chat.kb.reindexFailedSuffix", { failed: res.failed })}`
            : ""),
      );
      await load();
    } catch (err) {
      setActionMessage(
        err instanceof Error
          ? t("chat.kb.reindexError", { message: err.message })
          : t("chat.kb.reindexErrorPlain"),
      );
    } finally {
      setReindexing(false);
      setReindexConfirmOpen(false);
    }
  }

  async function doDelete() {
    if (!data) return;
    setDeleting(true);
    setActionMessage(null);
    try {
      await deleteAdminKnowledgeBase(id);
      router.push("/chat-knowledge");
    } catch (err) {
      setActionMessage(
        err instanceof Error
          ? t("chat.kb.deleteError", { message: err.message })
          : t("chat.kb.deleteErrorPlain"),
      );
      setDeleting(false);
      setDeleteConfirmOpen(false);
    }
  }

  if (loading && !data) {
    return (
      <div>
        <PageHeader
          title={t("chat.kb.detailTitle")}
          subtitle={t("state.loading")}
        />
        <div className="sran-card" style={{ padding: "0.75rem" }}>
          <SkeletonTable rows={6} cols={5} />
        </div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div>
        <PageHeader
          title={t("chat.kb.detailTitle")}
          subtitle={t("chat.kb.detailLoadError")}
        />
        <ErrorState
          message={error ?? t("chat.kb.notFound")}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  const kb = data.knowledge_base;

  return (
    <div>
      <PageHeader
        title={kb.name}
        subtitle={kb.description || t("chat.kb.noDescription")}
        actions={
          <Link
            href="/chat-knowledge"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            ← {t("chat.kb.allKbs")}
          </Link>
        }
      />

      {/* Action message */}
      {actionMessage ? (
        <div
          className="sran-card"
          style={{
            padding: "0.6rem 0.875rem",
            marginBottom: "0.875rem",
            background: "var(--color-status-info-bg)",
            border: "1px solid var(--color-status-info)",
            color: "var(--color-text-primary)",
            fontSize: "0.875rem",
          }}
        >
          {actionMessage}
        </div>
      ) : null}

      {/* Stat strip */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.75rem",
          marginBottom: "0.875rem",
        }}
      >
        <StatCard
          label={t("chat.col.documents")}
          value={kb.document_count.toString()}
        />
        <StatCard label={t("chat.col.chunks")} value={kb.total_chunks.toString()} />
        <StatCard
          label={t("chat.kb.chunkSize")}
          value={t("chat.kb.chars", { n: kb.chunk_size })}
        />
        <StatCard
          label={t("chat.kb.overlap")}
          value={t("chat.kb.chars", { n: kb.chunk_overlap })}
        />
        <StatCard
          label={t("field.status")}
          value={kb.is_active ? t("field.active") : t("field.inactive")}
        />
      </div>

      {/* KB metadata card */}
      <div
        className="sran-card"
        style={{ padding: "0.875rem 1rem", marginBottom: "0.875rem" }}
      >
        <DLRow label={t("field.id")} value={<Mono>{kb.id}</Mono>} />
        <DLRow
          label={t("field.tenant")}
          value={
            <>
              {kb.tenant_name ? (
                <>
                  {kb.tenant_name}{" "}
                  <span style={{ color: "var(--color-text-muted)" }}>
                    (<Mono>{kb.tenant_id}</Mono>)
                  </span>
                </>
              ) : (
                <Mono>{kb.tenant_id}</Mono>
              )}
            </>
          }
        />
        <DLRow
          label={t("chat.kb.embeddingModel")}
          value={<Mono>{kb.embedding_model}</Mono>}
        />
        <DLRow label={t("field.created")} value={formatDate(kb.created_at)} />
        <DLRow label={t("field.updated")} value={formatDate(kb.updated_at)} last />
      </div>

      {/* Documents table */}
      <h2
        style={{
          fontSize: "0.875rem",
          fontWeight: 600,
          margin: "1rem 0 0.5rem 0",
          color: "var(--color-text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        {t("chat.kb.documentsCount", { n: data.documents.length })}
      </h2>
      <div className="sran-card" style={{ padding: 0, marginBottom: "0.875rem" }}>
        {data.documents.length === 0 ? (
          <div
            style={{
              padding: "1.25rem 1rem",
              color: "var(--color-text-muted)",
              fontSize: "0.875rem",
            }}
          >
            {t("chat.kb.noDocuments")}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="sran-table" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>{t("chat.col.filename")}</th>
                  <th>{t("field.status")}</th>
                  <th style={{ textAlign: "right" }}>{t("chat.col.chunks")}</th>
                  <th style={{ textAlign: "right" }}>{t("chat.col.size")}</th>
                  <th>{t("chat.col.uploaded")}</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((doc) => (
                  <DocRow key={doc.id} doc={doc} t={t} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Sample chunks */}
      <h2
        style={{
          fontSize: "0.875rem",
          fontWeight: 600,
          margin: "1rem 0 0.5rem 0",
          color: "var(--color-text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        {t("chat.kb.sampleChunks")}
      </h2>
      <div className="sran-card" style={{ padding: 0, marginBottom: "0.875rem" }}>
        {data.sample_chunks.length === 0 ? (
          <div
            style={{
              padding: "1.25rem 1rem",
              color: "var(--color-text-muted)",
              fontSize: "0.875rem",
            }}
          >
            {t("chat.kb.noChunks")}
          </div>
        ) : (
          data.sample_chunks.map((c, i) => (
            <div
              key={c.id}
              style={{
                padding: "0.75rem 1rem",
                borderBottom:
                  i === data.sample_chunks.length - 1
                    ? "none"
                    : "1px solid var(--color-border)",
              }}
            >
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--color-text-muted)",
                  marginBottom: 4,
                }}
              >
                <Mono>{c.filename}</Mono> ·{" "}
                {t("chat.kb.section", { n: c.chunk_index + 1 })}
              </div>
              <div
                style={{
                  fontSize: "0.825rem",
                  color: "var(--color-text-secondary)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  lineHeight: 1.45,
                }}
              >
                {c.content}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Actions */}
      <div
        className="sran-card"
        style={{
          padding: "0.875rem 1rem",
          border: "1px solid var(--color-status-error)",
          background: "var(--color-status-error-bg)",
        }}
      >
        <h2
          style={{
            fontSize: "0.875rem",
            fontWeight: 600,
            color: "var(--color-text-primary)",
            marginBottom: "0.5rem",
          }}
        >
          {t("chat.kb.adminActions")}
        </h2>
        <p
          style={{
            fontSize: "0.8rem",
            color: "var(--color-text-secondary)",
            marginBottom: "0.75rem",
          }}
        >
          {t("chat.kb.adminActionsBody")}
        </p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            type="button"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            disabled={reindexing || deleting}
            onClick={() => setReindexConfirmOpen(true)}
          >
            {reindexing ? t("chat.kb.reindexing") : t("chat.kb.reindexAll")}
          </button>
          <button
            type="button"
            className="sran-btn sran-btn-sm"
            disabled={reindexing || deleting}
            onClick={() => setDeleteConfirmOpen(true)}
            style={{
              background: "var(--color-status-error-bg)",
              color: "var(--color-status-error)",
              border: "1px solid var(--color-status-error)",
            }}
          >
            {deleting ? t("action.deleting") : t("chat.kb.deleteKb")}
          </button>
          <Link
            href="/chat-knowledge"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            {t("chat.kb.backToList")}
          </Link>
        </div>
      </div>

      <ConfirmModal
        open={reindexConfirmOpen}
        onClose={() => !reindexing && setReindexConfirmOpen(false)}
        onConfirm={() => void doReindex()}
        title={t("chat.kb.confirmReindexTitle")}
        message={
          data.documents.length === 1
            ? t("chat.kb.confirmReindexBodyOne", { name: kb.name })
            : t("chat.kb.confirmReindexBody", {
                name: kb.name,
                count: data.documents.length,
              })
        }
        confirmLabel={t("chat.kb.reindex")}
        loading={reindexing}
      />

      <ConfirmModal
        open={deleteConfirmOpen}
        onClose={() => !deleting && setDeleteConfirmOpen(false)}
        onConfirm={() => void doDelete()}
        title={t("chat.kb.confirmDeleteTitle")}
        message={t("chat.kb.confirmDeleteBody", {
          name: kb.name,
          documents: kb.document_count,
          chunks: kb.total_chunks,
        })}
        confirmLabel={t("action.delete")}
        loading={deleting}
      />
    </div>
  );
}

function DocRow({ doc, t }: { doc: AdminKBDocRow; t: Translate }) {
  return (
    <tr>
      <td>
        <div style={{ color: "var(--color-text-primary)" }}>{doc.filename}</div>
        {doc.error_message ? (
          <div
            style={{
              fontSize: "0.7rem",
              color: "var(--color-status-error)",
              marginTop: 2,
              maxWidth: 360,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={doc.error_message}
          >
            {doc.error_message}
          </div>
        ) : null}
      </td>
      <td>
        <DocStatusPill status={doc.status} t={t} />
      </td>
      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {doc.chunk_count}
      </td>
      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {humanSize(doc.file_size_bytes)}
      </td>
      <td style={{ color: "var(--color-text-muted)" }}>
        {formatDate(doc.created_at)}
      </td>
    </tr>
  );
}

// The indexing status is UI chrome, not an identifier an operator would
// paste into a bug report, so it is localized. Unknown statuses (a newer
// backend enum member) fall through to the raw value rather than a blank.
const DOC_STATUS_KEY: Record<AdminKBDocRow["status"], DictKey> = {
  pending: "chat.kb.docStatus.pending",
  processing: "chat.kb.docStatus.processing",
  indexed: "chat.kb.docStatus.indexed",
  failed: "chat.kb.docStatus.failed",
};

function DocStatusPill({
  status,
  t,
}: {
  status: AdminKBDocRow["status"];
  t: Translate;
}) {
  const palette: Record<AdminKBDocRow["status"], { bg: string; fg: string; border: string }> = {
    pending: {
      bg: "var(--bg-elevated)",
      fg: "var(--color-text-muted)",
      border: "var(--color-border)",
    },
    processing: {
      bg: "var(--color-status-warning-bg)",
      fg: "var(--color-status-warning)",
      border: "var(--color-status-warning)",
    },
    indexed: {
      bg: "var(--color-status-success-bg)",
      fg: "var(--color-status-success)",
      border: "var(--color-status-success)",
    },
    failed: {
      bg: "var(--color-status-error-bg)",
      fg: "var(--color-status-error)",
      border: "var(--color-status-error)",
    },
  };
  const p = palette[status] ?? palette.pending;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: "0.7rem",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.03em",
        background: p.bg,
        color: p.fg,
        border: `1px solid ${p.border}`,
      }}
    >
      {DOC_STATUS_KEY[status] ? t(DOC_STATUS_KEY[status]) : status}
    </span>
  );
}

function DLRow({
  label,
  value,
  last,
}: {
  label: string;
  value: React.ReactNode;
  last?: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "140px 1fr",
        gap: "0.875rem",
        padding: "0.45rem 0",
        borderBottom: last ? "none" : "1px solid var(--color-border)",
        fontSize: "0.85rem",
      }}
    >
      <span
        style={{
          color: "var(--color-text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          fontSize: "0.7rem",
          fontWeight: 600,
        }}
      >
        {label}
      </span>
      <span style={{ color: "var(--color-text-primary)" }}>{value}</span>
    </div>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: "0.82em" }}>
      {children}
    </span>
  );
}
