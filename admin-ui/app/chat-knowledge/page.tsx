"use client";

// Admin Knowledge Bases — cross-tenant list view.
//
// Mirrors the chat-conversations page structure: filter card,
// paginated table, click-through to a detail page. Tenant filter is
// a free-text UUID field rather than a dropdown — listing every
// tenant just to pick one would balloon for a SaaS deployment. The
// chat-users page already shows tenant ids next to each user, so an
// admin can copy/paste from there.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import PageHeader from "../../components/ui/PageHeader";
import Pagination from "../../components/ui/Pagination";
import ErrorState from "../../components/ui/ErrorState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import {
  AdminKBListResponse,
  AdminKBQuery,
  fetchAdminKnowledgeBases,
} from "../../lib/chatEdgeApi";

const PAGE_SIZE = 50;

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export default function ChatKnowledgePage() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<AdminKBQuery>({});
  const [draft, setDraft] = useState<AdminKBQuery>({});
  const [data, setData] = useState<AdminKBListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAdminKnowledgeBases({
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

  // Drives the empty-state copy: a filtered-but-empty result invites
  // clearing the filters, whereas a genuinely empty list explains where
  // knowledge bases come from.
  const hasActiveFilters = Boolean(filters.q || filters.tenant_id);

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
        title="Knowledge Bases"
        subtitle="Inspect retrieval-augmented knowledge bases across every tenant"
      />

      <div
        className="sran-card"
        style={{ padding: "0.875rem 1rem", marginBottom: "0.875rem" }}
      >
        <div
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
          style={{
            gap: "0.5rem",
            alignItems: "end",
          }}
        >
          <Field label="Search by name">
            <input
              type="text"
              value={draft.q ?? ""}
              onChange={(e) => setDraft({ ...draft, q: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyFilters();
              }}
              placeholder="Search knowledge bases..."
              className="sran-input"
              style={inputStyle}
            />
          </Field>
          <Field label="Tenant ID (uuid)">
            <input
              type="text"
              value={draft.tenant_id ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, tenant_id: e.target.value || undefined })
              }
              placeholder="00000000-0000-0000-..."
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

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <div className="sran-card" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: "0.5rem 0.75rem" }}>
              <SkeletonTable rows={10} cols={7} />
            </div>
          ) : !data || data.knowledge_bases.length === 0 ? (
            hasActiveFilters ? (
              <KbEmptyState
                title="No knowledge bases match these filters"
                description="Try a broader search term or a different tenant id."
                action={
                  <button
                    type="button"
                    onClick={resetFilters}
                    className="sran-btn sran-btn-secondary sran-btn-sm"
                  >
                    Clear filters
                  </button>
                }
              />
            ) : (
              <KbEmptyState
                title="No knowledge bases yet"
                description="Knowledge bases created by users in SRAN Chat will appear here, with their per-tenant documents and chunk counts."
              />
            )
          ) : (
            <>
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 920 }}>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Tenant</th>
                      <th style={{ textAlign: "right" }}>Documents</th>
                      <th style={{ textAlign: "right" }}>Chunks</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.knowledge_bases.map((kb) => (
                      <tr key={kb.id}>
                        <td>
                          <Link
                            href={`/chat-knowledge/${kb.id}`}
                            style={{
                              color: "var(--color-text-primary)",
                              fontWeight: 500,
                            }}
                          >
                            {kb.name}
                          </Link>
                          {kb.description ? (
                            <div
                              style={{
                                fontSize: "0.75rem",
                                color: "var(--color-text-muted)",
                                marginTop: 2,
                                maxWidth: 360,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {kb.description}
                            </div>
                          ) : null}
                        </td>
                        <td style={{ color: "var(--color-text-secondary)" }}>
                          {kb.tenant_name || kb.tenant_id}
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {kb.document_count}
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {kb.total_chunks}
                        </td>
                        <td>
                          <StatusPill active={kb.is_active} />
                        </td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatDate(kb.created_at)}
                        </td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatDate(kb.updated_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div
                style={{
                  padding: "0.5rem 0.75rem",
                  borderTop: "1px solid var(--color-border)",
                }}
              >
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

// Empty state local to this page so the icon can be a full-size inline
// SVG. The shared <EmptyState> renders its icon inside a small (~44px)
// circular chip, which makes a glyph look cramped; here we want a larger,
// purpose-built knowledge icon. SVG follows the same Lucide-style stroke
// convention used in the Sidebar (viewBox 0 0 24 24, currentColor).
function KbEmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        padding: "3.5rem 1.5rem",
        gap: "0.625rem",
      }}
    >
      <svg
        width="56"
        height="56"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ color: "var(--color-text-tertiary)", marginBottom: "0.25rem" }}
        aria-hidden="true"
      >
        {/* Lucide "book-open" — represents the knowledge bases. */}
        <path d="M12 7v14" />
        <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" />
      </svg>
      <h3
        style={{
          margin: 0,
          fontSize: "0.95rem",
          fontWeight: 600,
          color: "var(--color-text-secondary)",
        }}
      >
        {title}
      </h3>
      {description ? (
        <p
          style={{
            margin: 0,
            fontSize: "0.8rem",
            color: "var(--color-text-muted)",
            maxWidth: 380,
            lineHeight: 1.5,
          }}
        >
          {description}
        </p>
      ) : null}
      {action ? <div style={{ marginTop: "0.75rem" }}>{action}</div> : null}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontSize: "0.65rem",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          color: "var(--color-text-muted)",
          fontWeight: 600,
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

function StatusPill({ active }: { active: boolean }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: "0.7rem",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.03em",
        background: active
          ? "rgba(34,197,94,0.12)"
          : "rgba(148,163,184,0.16)",
        color: active ? "rgb(74,222,128)" : "var(--color-text-muted)",
        border: active
          ? "1px solid rgba(34,197,94,0.3)"
          : "1px solid var(--color-border)",
      }}
    >
      {active ? "Active" : "Inactive"}
    </span>
  );
}

const inputStyle: React.CSSProperties = { width: "100%" };
