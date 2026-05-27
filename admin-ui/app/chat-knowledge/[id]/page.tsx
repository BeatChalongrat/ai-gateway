"use client";

// Admin KB detail — header + stats + documents table + sample chunks
// peek + danger zone (reindex, delete).
//
// The reindex call is synchronous server-side and can take minutes
// for big KBs; the UI shows a spinning button while it runs and
// re-loads on completion. Delete double-confirms via window.confirm
// — sufficient for a v1 admin tool.

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import PageHeader from "../../../components/ui/PageHeader";
import ErrorState from "../../../components/ui/ErrorState";
import { SkeletonTable } from "../../../components/ui/Skeleton";
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

export default function ChatKnowledgeDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = typeof params?.id === "string" ? params.id : "";

  const [data, setData] = useState<AdminKBDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAdminKnowledgeBase(id);
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleReindex() {
    if (!data) return;
    if (
      !window.confirm(
        `Reindex every document in "${data.knowledge_base.name}"? ` +
          `This re-embeds ${data.documents.length} document${data.documents.length === 1 ? "" : "s"} and may take several minutes.`,
      )
    ) {
      return;
    }
    setReindexing(true);
    setActionMessage(null);
    try {
      const res = await reindexAdminKnowledgeBase(id);
      setActionMessage(
        `Reindexed ${res.reindexed} of ${res.total} documents` +
          (res.failed > 0 ? ` (${res.failed} failed)` : ""),
      );
      await load();
    } catch (err) {
      setActionMessage(
        err instanceof Error ? `Reindex failed: ${err.message}` : "Reindex failed",
      );
    } finally {
      setReindexing(false);
    }
  }

  async function handleDelete() {
    if (!data) return;
    if (
      !window.confirm(
        `Delete "${data.knowledge_base.name}"? ` +
          `${data.knowledge_base.document_count} documents and ${data.knowledge_base.total_chunks} chunks will be permanently removed.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    setActionMessage(null);
    try {
      await deleteAdminKnowledgeBase(id);
      router.push("/chat-knowledge");
    } catch (err) {
      setActionMessage(
        err instanceof Error ? `Delete failed: ${err.message}` : "Delete failed",
      );
      setDeleting(false);
    }
  }

  if (loading && !data) {
    return (
      <div>
        <PageHeader
          title="Knowledge Base"
          subtitle="Loading…"
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
          title="Knowledge Base"
          subtitle="Could not load this knowledge base"
        />
        <ErrorState message={error ?? "Not found"} onRetry={() => void load()} />
      </div>
    );
  }

  const kb = data.knowledge_base;

  return (
    <div>
      <PageHeader
        title={kb.name}
        subtitle={kb.description || "No description"}
        actions={
          <Link
            href="/chat-knowledge"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            ← All KBs
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
            background: "rgba(59,130,246,0.08)",
            border: "1px solid rgba(59,130,246,0.25)",
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
        <StatCard label="Documents" value={kb.document_count.toString()} />
        <StatCard label="Chunks" value={kb.total_chunks.toString()} />
        <StatCard label="Chunk size" value={`${kb.chunk_size} chars`} />
        <StatCard label="Overlap" value={`${kb.chunk_overlap} chars`} />
        <StatCard
          label="Status"
          value={kb.is_active ? "Active" : "Inactive"}
        />
      </div>

      {/* KB metadata card */}
      <div
        className="sran-card"
        style={{ padding: "0.875rem 1rem", marginBottom: "0.875rem" }}
      >
        <DLRow label="ID" value={<Mono>{kb.id}</Mono>} />
        <DLRow
          label="Tenant"
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
        <DLRow label="Embedding model" value={<Mono>{kb.embedding_model}</Mono>} />
        <DLRow label="Created" value={formatDate(kb.created_at)} />
        <DLRow label="Updated" value={formatDate(kb.updated_at)} last />
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
        Documents ({data.documents.length})
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
            No documents indexed.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="sran-table" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Chunks</th>
                  <th style={{ textAlign: "right" }}>Size</th>
                  <th>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((doc) => (
                  <DocRow key={doc.id} doc={doc} />
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
        Sample chunks
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
            No chunks yet — upload a document to populate this list.
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
                <Mono>{c.filename}</Mono> · section {c.chunk_index + 1}
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
          border: "1px solid rgba(248,113,113,0.25)",
          background: "rgba(248,113,113,0.04)",
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
          Admin actions
        </h2>
        <p
          style={{
            fontSize: "0.8rem",
            color: "var(--color-text-secondary)",
            marginBottom: "0.75rem",
          }}
        >
          Reindex re-embeds every document — useful after a chunk-size config
          change. Delete cascades to documents, chunks, and on-disk / S3 blobs.
        </p>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            type="button"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            disabled={reindexing || deleting}
            onClick={() => void handleReindex()}
          >
            {reindexing ? "Reindexing…" : "Reindex all documents"}
          </button>
          <button
            type="button"
            className="sran-btn sran-btn-sm"
            disabled={reindexing || deleting}
            onClick={() => void handleDelete()}
            style={{
              background: "rgba(248,113,113,0.12)",
              color: "rgb(252,165,165)",
              border: "1px solid rgba(248,113,113,0.35)",
            }}
          >
            {deleting ? "Deleting…" : "Delete knowledge base"}
          </button>
          <Link
            href="/chat-knowledge"
            className="sran-btn sran-btn-secondary sran-btn-sm"
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            Back to list
          </Link>
        </div>
      </div>
    </div>
  );
}

function DocRow({ doc }: { doc: AdminKBDocRow }) {
  return (
    <tr>
      <td>
        <div style={{ color: "var(--color-text-primary)" }}>{doc.filename}</div>
        {doc.error_message ? (
          <div
            style={{
              fontSize: "0.7rem",
              color: "rgb(252,165,165)",
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
        <DocStatusPill status={doc.status} />
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

function DocStatusPill({ status }: { status: AdminKBDocRow["status"] }) {
  const palette: Record<AdminKBDocRow["status"], { bg: string; fg: string; border: string }> = {
    pending: {
      bg: "rgba(148,163,184,0.16)",
      fg: "var(--color-text-muted)",
      border: "var(--color-border)",
    },
    processing: {
      bg: "rgba(245,158,11,0.14)",
      fg: "rgb(252,211,77)",
      border: "rgba(245,158,11,0.35)",
    },
    indexed: {
      bg: "rgba(34,197,94,0.12)",
      fg: "rgb(74,222,128)",
      border: "rgba(34,197,94,0.3)",
    },
    failed: {
      bg: "rgba(248,113,113,0.12)",
      fg: "rgb(252,165,165)",
      border: "rgba(248,113,113,0.35)",
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
      {status}
    </span>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="sran-card" style={{ padding: "0.7rem 0.875rem" }}>
      <div
        style={{
          fontSize: "0.7rem",
          color: "var(--color-text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: "1.05rem",
          fontWeight: 600,
          color: "var(--color-text-primary)",
        }}
      >
        {value}
      </div>
    </div>
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
