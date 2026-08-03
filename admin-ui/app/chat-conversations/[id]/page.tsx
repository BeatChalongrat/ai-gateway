"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import PageHeader from "../../../components/ui/PageHeader";
import ErrorState from "../../../components/ui/ErrorState";
import { SkeletonTable } from "../../../components/ui/Skeleton";
import { Icon } from "../../../components/ui/Icon";
import { useT } from "../../../lib/i18n";
import {
  AdminConversationDetail,
  fetchAdminConversation,
} from "../../../lib/chatEdgeApi";

type Translate = ReturnType<typeof useT>;

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

function roleColor(
  role: string,
  t: Translate,
): { bg: string; tint: string; fg: string; label: string } {
  // Token-driven so the role tints read correctly in BOTH themes (the prior
  // rgba literals were tuned for dark and went low-contrast on light). bg =
  // pill fill, tint = message-tile fill, fg = pill text + left accent border.
  switch (role) {
    case "user":
      return {
        bg: "color-mix(in srgb, var(--accent-primary) 16%, transparent)",
        tint: "color-mix(in srgb, var(--accent-primary) 7%, transparent)",
        fg: "var(--accent-primary)",
        label: t("chat.role.user"),
      };
    case "assistant":
      return {
        bg: "color-mix(in srgb, var(--accent-success) 16%, transparent)",
        tint: "color-mix(in srgb, var(--accent-success) 7%, transparent)",
        fg: "var(--accent-success)",
        label: t("chat.role.assistant"),
      };
    default:
      return {
        bg: "var(--bg-elevated)",
        tint: "color-mix(in srgb, var(--text-muted) 8%, transparent)",
        fg: "var(--text-secondary)",
        label: role,
      };
  }
}

export default function ChatConversationDetailPage() {
  const t = useT();
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
      setError(err instanceof Error ? err.message : t("state.errorLoad"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <PageHeader
        title={data?.conversation.title ?? t("chat.conversation.title")}
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
            ← {t("chat.conversation.backToLogs")}
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
        <span>{t("chat.conversation.maskedBanner")}</span>
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
            <Summary label={t("field.user")} value={data.conversation.user_email || "—"} />
            <Summary
              label={t("chat.conversation.userName")}
              value={data.conversation.user_name || "—"}
            />
            <Summary label={t("field.model")} value={data.conversation.model_id || "—"} />
            <Summary
              label={t("chat.col.messages")}
              value={String(data.conversation.message_count)}
            />
            <Summary
              label={t("field.created")}
              value={formatDate(data.conversation.created_at)}
            />
            <Summary
              label={t("chat.col.lastActive")}
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
              {t("chat.col.messages")}
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {data.messages.map((m) => {
                const rc = roleColor(m.role, t);
                return (
                  <div
                    key={m.id}
                    style={{
                      border: "1px solid var(--color-border)",
                      borderLeft: `3px solid ${rc.fg}`,
                      borderRadius: 8,
                      padding: "0.75rem 0.875rem",
                      background: rc.tint,
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
                          fontSize: "0.75rem",
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
                            fontSize: "0.75rem",
                            color: "var(--color-text-muted)",
                          }}
                        >
                          {m.model_id}
                        </span>
                      ) : null}
                      <span
                        style={{
                          fontSize: "0.75rem",
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
                          fontSize: "0.75rem",
                          color: "var(--color-text-muted)",
                        }}
                      >
                        {t("chat.conversation.tokenSummary", {
                          input: m.token_input ?? 0,
                          output: m.token_output ?? 0,
                        })}
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
                {t("chat.conversation.attachments")}
              </h3>
              <table className="sran-table">
                <thead>
                  <tr>
                    <th>{t("chat.col.filename")}</th>
                    <th>{t("field.type")}</th>
                    <th style={{ textAlign: "right" }}>{t("chat.col.size")}</th>
                    <th>{t("chat.col.uploaded")}</th>
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
                  fontSize: "0.75rem",
                  color: "var(--color-text-muted)",
                }}
              >
                {t("chat.conversation.downloadsDisabled")}
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
          fontSize: "0.75rem",
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
