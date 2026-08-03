"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import PageHeader from "../../components/ui/PageHeader";
import Pagination from "../../components/ui/Pagination";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useT } from "../../lib/i18n";
import {
  AdminConvQuery,
  AdminConversationListResponse,
  fetchAdminConversations,
} from "../../lib/chatEdgeApi";

const PAGE_SIZE = 50;

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export default function ChatConversationsPage() {
  const t = useT();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<AdminConvQuery>({});
  // Local filter draft — applied only when "Apply Filters" clicks
  // so the page doesn't re-fetch on every keystroke in the search
  // input.
  const [draft, setDraft] = useState<AdminConvQuery>({});
  const [data, setData] = useState<AdminConversationListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAdminConversations({
        ...filters,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("state.errorLoad"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [filters, page, t]);

  useEffect(() => {
    void load();
  }, [load]);

  function applyFilters() {
    setPage(1);
    setFilters(draft);
  }

  function resetFilters() {
    setDraft({});
    setFilters({});
    setPage(1);
  }

  return (
    <div>
      <PageHeader
        title={t("chat.conversations.title")}
        subtitle={t("chat.conversations.subtitle")}
      />

      {/* Filters */}
      <div
        className="sran-card"
        style={{ padding: "0.875rem 1rem", marginBottom: "0.875rem" }}
      >
        <div
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5"
          style={{
            gap: "0.5rem",
            alignItems: "end",
          }}
        >
          <Field label={t("chat.conversations.searchTitleLabel")}>
            <input
              type="text"
              value={draft.q ?? ""}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyFilters();
              }}
              placeholder={t("chat.conversations.searchPlaceholder")}
              className="sran-input"
            />
          </Field>
          <Field label={t("chat.conversations.modelIdLabel")}>
            <input
              type="text"
              value={draft.model_id ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, model_id: e.target.value || undefined })
              }
              placeholder={t("chat.conversations.modelIdPlaceholder")}
              className="sran-input"
            />
          </Field>
          <Field label={t("chat.conversations.from")}>
            <input
              type="date"
              value={draft.date_from ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, date_from: e.target.value || undefined })
              }
              className="sran-input"
            />
          </Field>
          <Field label={t("chat.conversations.to")}>
            <input
              type="date"
              value={draft.date_to ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, date_to: e.target.value || undefined })
              }
              className="sran-input"
            />
          </Field>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              onClick={applyFilters}
              className="sran-btn sran-btn-primary sran-btn-sm"
            >
              {t("action.apply")}
            </button>
            <button
              type="button"
              onClick={resetFilters}
              className="sran-btn sran-btn-secondary sran-btn-sm"
            >
              {t("action.reset")}
            </button>
          </div>
        </div>
      </div>

      {/* Table */}
      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <div className="sran-card" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: "0.5rem 0.75rem" }}>
              <SkeletonTable rows={10} cols={8} />
            </div>
          ) : !data || data.conversations.length === 0 ? (
            <div style={{ padding: "2rem 1rem" }}>
              <EmptyState message={t("state.noResults")} />
            </div>
          ) : (
            <>
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 920 }}>
                  <thead>
                    <tr>
                      <th>{t("chat.col.title")}</th>
                      <th>{t("field.user")}</th>
                      <th>{t("field.model")}</th>
                      <th style={{ textAlign: "right" }}>{t("chat.col.msgs")}</th>
                      <th style={{ textAlign: "right" }}>{t("chat.col.tokens")}</th>
                      <th>{t("chat.col.files")}</th>
                      <th>{t("chat.col.shared")}</th>
                      <th>{t("field.created")}</th>
                      <th>{t("chat.col.lastActive")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.conversations.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <Link
                            href={`/chat-conversations/${c.id}`}
                            style={{ color: "var(--color-text-primary)", fontWeight: 500 }}
                          >
                            {c.title || t("chat.untitled")}
                          </Link>
                        </td>
                        <td style={{ color: "var(--color-text-secondary)" }}>
                          {c.user_email || "—"}
                        </td>
                        <td style={{ color: "var(--color-text-secondary)" }}>
                          {c.model_id}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {c.message_count}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {compact(c.token_count)}
                        </td>
                        <td style={{ textAlign: "center" }}><BoolCell value={c.has_files} /></td>
                        <td style={{ textAlign: "center" }}><BoolCell value={c.is_shared} /></td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatDate(c.created_at)}
                        </td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatDate(c.updated_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ padding: "0.5rem 0.75rem", borderTop: "1px solid var(--color-border)" }}>
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={data.total}
                  onPageChange={setPage}
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function BoolCell({ value }: { value: boolean }) {
  return value ? (
    <span
      className="sran-badge sran-badge-success"
      style={{ padding: "0.0625rem 0.375rem" }}
    >
      <Icon name="check" size={12} />
    </span>
  ) : (
    <span style={{ color: "var(--color-text-muted)" }}>—</span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontSize: "0.65rem",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--color-text-muted)",
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}
