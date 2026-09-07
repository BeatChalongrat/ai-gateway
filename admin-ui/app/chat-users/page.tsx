"use client";

import { useCallback, useEffect, useState } from "react";

import ConfirmModal from "../../components/ui/ConfirmModal";
import PageHeader from "../../components/ui/PageHeader";
import Pagination from "../../components/ui/Pagination";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { useT } from "../../lib/i18n";
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

type Translate = ReturnType<typeof useT>;

function formatRelative(iso: string | undefined, t: Translate): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const diff = Date.now() - ms;
  if (diff < 60_000) return t("time.justNow");
  if (diff < 3_600_000) return t("time.minutesAgo", { n: Math.floor(diff / 60_000) });
  if (diff < 86_400_000) return t("time.hoursAgo", { n: Math.floor(diff / 3_600_000) });
  return t("time.daysAgo", { n: Math.floor(diff / 86_400_000) });
}

export default function ChatUsersPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [data, setData] = useState<AdminUserListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const { toast } = useToast();
  const t = useT();

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
      setError(err instanceof Error ? err.message : t("state.errorLoad"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [page, appliedSearch, t]);

  useEffect(() => {
    void load();
  }, [load]);

  // FIX84 Part D — disabling a user is destructive (they lose chat access
  // at once, with no undo from their side) and used to fire on the first
  // click. It now asks first, naming the user and the consequence.
  // Re-enabling restores access, so it stays one click.
  const [disableTarget, setDisableTarget] = useState<AdminUserRow | null>(null);

  function requestToggle(u: AdminUserRow) {
    if (u.is_active) setDisableTarget(u);
    else void toggleActive(u);
  }

  async function toggleActive(u: AdminUserRow) {
    setDisableTarget(null);
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
    } catch (err) {
      toast(
        err instanceof Error ? err.message : t("chat.users.updateFailed"),
        "error",
      );
      // refetch to recover from any drift
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
        title={t("chat.users.title")}
        subtitle={t("chat.users.subtitle")}
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
          placeholder={t("chat.users.searchPlaceholder")}
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
          {t("action.search")}
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
            {t("action.clear")}
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
              <EmptyState message={t("state.noResults")} />
            </div>
          ) : (
            <>
              <div style={{ overflowX: "auto" }}>
                <table className="sran-table" style={{ minWidth: 820 }}>
                  <thead>
                    <tr>
                      <th>{t("field.user")}</th>
                      <th>{t("field.role")}</th>
                      <th style={{ textAlign: "right" }}>
                        {t("chat.col.conversations")}
                      </th>
                      <th style={{ textAlign: "right" }}>{t("chat.col.messages")}</th>
                      <th style={{ textAlign: "right" }}>{t("chat.col.tokens")}</th>
                      <th>{t("chat.col.lastActive")}</th>
                      <th>{t("field.status")}</th>
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
                          {formatRelative(u.last_active_at, t)}
                        </td>
                        <td>
                          <StatusPill active={u.is_active} label={u.is_active ? t("field.active") : t("field.disabled")} />
                        </td>
                        <td>
                          <button
                            type="button"
                            onClick={() => requestToggle(u)}
                            disabled={pendingId === u.id}
                            // State-aware, token-colored affordance for the
                            // active/inactive concept: enabling a disabled user
                            // is the positive (primary) action; disabling an
                            // active user is the neutral (secondary) action.
                            className={`sran-btn sran-btn-sm ${u.is_active ? "sran-btn-secondary" : "sran-btn-primary"}`}
                            title={
                              u.is_active
                                ? t("chat.users.disableUser")
                                : t("chat.users.enableUser")
                            }
                          >
                            {pendingId === u.id
                              ? "…"
                              : u.is_active
                                ? t("action.disable")
                                : t("action.enable")}
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
      <ConfirmModal
        open={!!disableTarget}
        onClose={() => setDisableTarget(null)}
        onConfirm={() => disableTarget && void toggleActive(disableTarget)}
        title={t("chat.users.disableTitle")}
        message={t("chat.users.disableBody", {
          name: disableTarget?.name || disableTarget?.email || "",
        })}
        confirmLabel={t("chat.users.disableConfirm")}
        loading={!!disableTarget && pendingId === disableTarget.id}
      />
    </div>
  );
}

function StatusPill({ active, label }: { active: boolean; label: string }) {
  const bg = active ? "var(--color-status-success-bg)" : "var(--color-status-error-bg)";
  const fg = active ? "var(--color-status-success)" : "var(--color-status-error)";
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
      {label}
    </span>
  );
}
