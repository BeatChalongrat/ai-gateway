"use client";

import { useCallback, useEffect, useState } from "react";

import PageHeader from "../../components/ui/PageHeader";
import Pagination from "../../components/ui/Pagination";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import {
  AdminUserListResponse,
  AdminUserRow,
  fetchAdminUsers,
  patchAdminUser,
} from "../../lib/chatEdgeApi";

const PAGE_SIZE = 50;

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatRelative(iso?: string): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export default function ChatUsersPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [data, setData] = useState<AdminUserListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchAdminUsers({
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        q: appliedSearch || undefined,
      });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [page, appliedSearch]);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleActive(u: AdminUserRow) {
    setPendingId(u.id);
    try {
      await patchAdminUser(u.id, { is_active: !u.is_active });
      // Optimistic in-place update
      setData((prev) =>
        prev
          ? {
              ...prev,
              users: prev.users.map((x) =>
                x.id === u.id ? { ...x, is_active: !u.is_active } : x,
              ),
            }
          : prev,
      );
    } catch {
      // best-effort; refetch to recover from any drift
      void load();
    } finally {
      setPendingId(null);
    }
  }

  function submitSearch() {
    setPage(1);
    setAppliedSearch(search.trim());
  }

  return (
    <div>
      <PageHeader
        title="Chat Users"
        subtitle="Per-user chat usage and access controls"
      />

      <div
        className="sran-card"
        style={{
          padding: "0.625rem 0.875rem",
          marginBottom: "0.875rem",
          display: "flex",
          gap: "0.5rem",
        }}
      >
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitSearch();
          }}
          placeholder="Search by email or name..."
          className="sran-input"
          style={{
            flex: 1,
            padding: "0.4rem 0.6rem",
            background: "var(--color-bg)",
            border: "1px solid var(--color-border)",
            borderRadius: 5,
            color: "var(--color-text-primary)",
            fontSize: "0.8rem",
          }}
        />
        <button
          type="button"
          onClick={submitSearch}
          className="sran-btn sran-btn-primary sran-btn-sm"
        >
          Search
        </button>
        {appliedSearch ? (
          <button
            type="button"
            onClick={() => {
              setSearch("");
              setAppliedSearch("");
              setPage(1);
            }}
            className="sran-btn sran-btn-secondary sran-btn-sm"
          >
            Clear
          </button>
        ) : null}
      </div>

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <div className="sran-card" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: "0.5rem 0.75rem" }}>
              <SkeletonTable rows={10} cols={7} />
            </div>
          ) : !data || data.users.length === 0 ? (
            <div style={{ padding: "2rem 1rem" }}>
              <EmptyState message="No users match" />
            </div>
          ) : (
            <>
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 820 }}>
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Role</th>
                      <th style={{ textAlign: "right" }}>Conversations</th>
                      <th style={{ textAlign: "right" }}>Messages</th>
                      <th style={{ textAlign: "right" }}>Tokens</th>
                      <th>Last Active</th>
                      <th>Status</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.users.map((u) => (
                      <tr key={u.id}>
                        <td>
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "0.5rem",
                            }}
                          >
                            <span
                              aria-hidden
                              style={{
                                width: 26,
                                height: 26,
                                borderRadius: 6,
                                background:
                                  "linear-gradient(135deg, #1a3570 0%, #2850a8 100%)",
                                color: "rgba(220,232,255,0.9)",
                                display: "inline-flex",
                                alignItems: "center",
                                justifyContent: "center",
                                fontSize: "0.7rem",
                                fontWeight: 600,
                              }}
                            >
                              {(u.name || u.email || "?").charAt(0).toUpperCase()}
                            </span>
                            <div style={{ minWidth: 0 }}>
                              <div
                                style={{
                                  fontWeight: 500,
                                  color: "var(--color-text-primary)",
                                }}
                              >
                                {u.name || u.email}
                              </div>
                              <div
                                style={{
                                  fontSize: "0.7rem",
                                  color: "var(--color-text-muted)",
                                }}
                              >
                                {u.email}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td
                          style={{
                            textTransform: "uppercase",
                            fontSize: "0.65rem",
                            letterSpacing: "0.04em",
                            color: "var(--color-text-secondary)",
                          }}
                        >
                          {u.role.replace("_", " ")}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {u.conversation_count}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {u.message_count}
                        </td>
                        <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {compact(u.token_count)}
                        </td>
                        <td style={{ color: "var(--color-text-muted)" }}>
                          {formatRelative(u.last_active_at)}
                        </td>
                        <td>
                          <StatusPill active={u.is_active} />
                        </td>
                        <td>
                          <button
                            type="button"
                            onClick={() => void toggleActive(u)}
                            disabled={pendingId === u.id}
                            className="sran-btn sran-btn-secondary sran-btn-sm"
                            title={u.is_active ? "Disable user" : "Enable user"}
                          >
                            {pendingId === u.id
                              ? "…"
                              : u.is_active
                                ? "Disable"
                                : "Enable"}
                          </button>
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

function StatusPill({ active }: { active: boolean }) {
  const bg = active ? "rgba(34,197,94,0.16)" : "rgba(239,68,68,0.16)";
  const fg = active ? "#86efac" : "#fca5a5";
  return (
    <span
      style={{
        background: bg,
        color: fg,
        padding: "0.12rem 0.5rem",
        borderRadius: 999,
        fontSize: "0.65rem",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        fontWeight: 600,
      }}
    >
      {active ? "Active" : "Disabled"}
    </span>
  );
}
