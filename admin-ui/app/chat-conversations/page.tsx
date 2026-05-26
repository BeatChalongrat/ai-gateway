"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import PageHeader from "../../components/ui/PageHeader";
import Pagination from "../../components/ui/Pagination";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
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
      setError(err instanceof Error ? err.message : "Failed to load");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

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
        title="Conversation Logs"
        subtitle="Filter and inspect chat conversations across the workspace"
      />

      {/* Filters */}
      <div
        className="sran-card"
        style={{ padding: "0.875rem 1rem", marginBottom: "0.875rem" }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "minmax(180px, 2fr) minmax(140px, 1fr) minmax(120px, 1fr) minmax(120px, 1fr) auto",
            gap: "0.5rem",
            alignItems: "end",
          }}
        >
          <Field label="Search title">
            <input
              type="text"
              value={draft.q ?? ""}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyFilters();
              }}
              placeholder="Search conversations..."
              className="sran-input"
              style={inputStyle}
            />
          </Field>
          <Field label="Model id">
            <input
              type="text"
              value={draft.model_id ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, model_id: e.target.value || undefined })
              }
              placeholder="e.g. gpt-4o"
              className="sran-input"
              style={inputStyle}
            />
          </Field>
          <Field label="From">
            <input
              type="date"
              value={draft.date_from ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, date_from: e.target.value || undefined })
              }
              className="sran-input"
              style={inputStyle}
            />
          </Field>
          <Field label="To">
            <input
              type="date"
              value={draft.date_to ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, date_to: e.target.value || undefined })
              }
              className="sran-input"
              style={inputStyle}
            />
          </Field>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              onClick={applyFilters}
              className="sran-btn sran-btn-primary sran-btn-sm"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={resetFilters}
              className="sran-btn sran-btn-secondary sran-btn-sm"
            >
              Reset
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
              <EmptyState message="No conversations match these filters" />
            </div>
          ) : (
            <>
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 920 }}>
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>User</th>
                      <th>Model</th>
                      <th style={{ textAlign: "right" }}>Msgs</th>
                      <th style={{ textAlign: "right" }}>Tokens</th>
                      <th>Files</th>
                      <th>Shared</th>
                      <th>Created</th>
                      <th>Last Active</th>
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
                            {c.title || "Untitled"}
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
                        <td>{c.has_files ? "✓" : "—"}</td>
                        <td>{c.is_shared ? "✓" : "—"}</td>
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

const inputStyle: React.CSSProperties = {
  padding: "0.375rem 0.55rem",
  background: "var(--color-bg)",
  border: "1px solid var(--color-border)",
  borderRadius: 5,
  color: "var(--color-text-primary)",
  fontSize: "0.8rem",
};
