"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import PageHeader from "../../../components/ui/PageHeader";
import ErrorState from "../../../components/ui/ErrorState";
import { SkeletonTable } from "../../../components/ui/Skeleton";
import { Icon } from "../../../components/ui/Icon";
import {
  AdminConversationDetail,
  fetchAdminConversation,
} from "../../../lib/chatEdgeApi";

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function roleColor(role: string): { bg: string; fg: string; label: string } {
  switch (role) {
    case "user":
      return {
        bg: "rgba(99, 102, 241, 0.18)",
        fg: "#a5b4fc",
        label: "User",
      };
    case "assistant":
      return {
        bg: "rgba(34, 197, 94, 0.16)",
        fg: "#86efac",
        label: "Assistant",
      };
    default:
      return {
        bg: "rgba(148, 163, 184, 0.18)",
        fg: "#cbd5e1",
        label: role,
      };
  }
}

export default function ChatConversationDetailPage() {
  const params = useParams();
  const id = typeof params?.id === "string" ? params.id : "";

  const [data, setData] = useState<AdminConversationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchAdminConversation(id);
      setData(d);
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

  return (
    <div>
      <PageHeader
        title={data?.conversation.title ?? "Conversation"}
        subtitle={
          data
            ? `${data.conversation.user_email} · ${data.conversation.model_id}`
            : undefined
        }
        actions={
          <Link
            href="/chat-conversations"
            className="sran-btn sran-btn-secondary sran-btn-sm"
          >
            ← Back to logs
          </Link>
        }
      />

      {/* Masked-content banner — always visible on the detail page
          so the operator never forgets they're looking at
          DLP-processed output. */}
      <div
        role="status"
        style={{
          display: "flex",
          gap: "0.5rem",
          alignItems: "flex-start",
          background: "rgba(245, 158, 11, 0.10)",
          border: "1px solid rgba(245, 158, 11, 0.32)",
          padding: "0.6rem 0.85rem",
          borderRadius: 6,
          marginBottom: "0.875rem",
          fontSize: "0.8rem",
          color: "var(--color-text-secondary)",
        }}
      >
        <span aria-hidden style={{ fontSize: "0.95rem" }}><Icon name="alert-triangle" size={14} /></span>
        <span>
          Viewing masked content — PII has been redacted by the DLP pipeline.
          Raw user prompts are never stored in this database.
        </span>
      </div>

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : loading || !data ? (
        <div className="sran-card" style={{ padding: "0.5rem 0.75rem" }}>
          <SkeletonTable rows={6} cols={3} />
        </div>
      ) : (
        <>
          {/* Header summary */}
          <div
            className="sran-card"
            style={{
              padding: "0.875rem 1rem",
              marginBottom: "0.875rem",
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <Summary label="User" value={data.conversation.user_email || "—"} />
            <Summary label="User name" value={data.conversation.user_name || "—"} />
            <Summary label="Model" value={data.conversation.model_id || "—"} />
            <Summary
              label="Messages"
              value={String(data.conversation.message_count)}
            />
            <Summary label="Created" value={formatDate(data.conversation.created_at)} />
            <Summary
              label="Last active"
              value={formatDate(data.conversation.updated_at)}
            />
          </div>

          {/* Messages */}
          <div
            className="sran-card"
            style={{ padding: "1rem 1.125rem", marginBottom: "0.875rem" }}
          >
            <h3
              style={{
                fontSize: "0.85rem",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                margin: "0 0 0.875rem",
              }}
            >
              Messages
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {data.messages.map((m) => {
                const rc = roleColor(m.role);
                return (
                  <div
                    key={m.id}
                    style={{
                      border: "1px solid var(--color-border)",
                      borderRadius: 8,
                      padding: "0.75rem 0.875rem",
                      background: "var(--color-bg)",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.5rem",
                        marginBottom: "0.5rem",
                        flexWrap: "wrap",
                      }}
                    >
                      <span
                        style={{
                          background: rc.bg,
                          color: rc.fg,
                          padding: "0.1rem 0.5rem",
                          borderRadius: 999,
                          fontSize: "0.65rem",
                          textTransform: "uppercase",
                          letterSpacing: "0.04em",
                          fontWeight: 600,
                        }}
                      >
                        {rc.label}
                      </span>
                      {m.model_id ? (
                        <span
                          style={{
                            fontSize: "0.65rem",
                            color: "var(--color-text-muted)",
                          }}
                        >
                          {m.model_id}
                        </span>
                      ) : null}
                      <span
                        style={{
                          fontSize: "0.65rem",
                          color: "var(--color-text-muted)",
                          marginLeft: "auto",
                        }}
                      >
                        {formatDate(m.created_at)}
                      </span>
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        fontFamily: "inherit",
                        fontSize: "0.825rem",
                        color: "var(--color-text-primary)",
                        lineHeight: 1.55,
                      }}
                    >
                      {m.content}
                    </pre>
                    {(m.token_input ?? 0) + (m.token_output ?? 0) > 0 ? (
                      <div
                        style={{
                          marginTop: "0.4rem",
                          fontSize: "0.6rem",
                          color: "var(--color-text-muted)",
                        }}
                      >
                        {m.token_input ?? 0} in · {m.token_output ?? 0} out tokens
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Files */}
          {data.files.length > 0 ? (
            <div className="sran-card" style={{ padding: "1rem 1.125rem" }}>
              <h3
                style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  margin: "0 0 0.625rem",
                  color: "var(--color-text-primary)",
                }}
              >
                Attachments
              </h3>
              <table className="sran-table">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>Type</th>
                    <th style={{ textAlign: "right" }}>Size</th>
                    <th>Uploaded</th>
                  </tr>
                </thead>
                <tbody>
                  {data.files.map((f) => (
                    <tr key={f.id}>
                      <td>{f.filename}</td>
                      <td style={{ color: "var(--color-text-muted)" }}>
                        {f.content_type}
                      </td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {formatBytes(f.file_size_bytes)}
                      </td>
                      <td style={{ color: "var(--color-text-muted)" }}>
                        {formatDate(f.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p
                style={{
                  marginTop: "0.5rem",
                  fontSize: "0.65rem",
                  color: "var(--color-text-muted)",
                }}
              >
                File downloads are intentionally disabled on the admin
                surface — admins see metadata only.
              </p>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: "0.6rem",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--color-text-muted)",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: "0.85rem",
          color: "var(--color-text-primary)",
          marginTop: 2,
          wordBreak: "break-word",
        }}
      >
        {value}
      </div>
    </div>
  );
}
