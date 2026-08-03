"use client";

import { useMemo, useState } from "react";

import Modal from "../../components/ui/Modal";
import ConfirmModal from "../../components/ui/ConfirmModal";
import EmptyState from "../../components/ui/EmptyState";
import ErrorState from "../../components/ui/ErrorState";
import StatCard from "../../components/ui/StatCard";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { useT, type DictKey } from "../../lib/i18n";
import { ThemeToggle } from "../../components/ThemeToggle";
import { Icon } from "../../components/ui/Icon";

type Translate = ReturnType<typeof useT>;

interface PortalUser {
  id: string;
  email: string;
  display_name: string | null;
  plan: string;
  status: string;
  credit_balance: number;
  credit_used: number;
  email_verified?: boolean;
  created_at: string;
  updated_at?: string;
  project_count?: number;
  key_count?: number;
}

interface UsersResp {
  users: PortalUser[];
  total: number;
}

interface Transaction {
  id: string;
  amount: number;
  balance_after: number;
  type: string;
  description: string | null;
  request_id: string | null;
  created_by: string | null;
  created_at: string;
}

interface UserDetail extends PortalUser {
  transactions: Transaction[];
}

type StatusFilter = "all" | "active" | "suspended" | "free_trial" | "paid";

function formatTokensCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return n.toLocaleString();
}

function formatTokensFull(n: number): string {
  return n.toLocaleString();
}

function creditPercent(balance: number, used: number): number {
  const lifetime = balance + used;
  if (lifetime <= 0) return 0;
  return Math.round((balance / lifetime) * 100);
}

function creditColor(pct: number, balance: number): string {
  if (balance <= 0) return "#EF4444";
  if (pct > 30) return "#10B981";
  if (pct >= 10) return "#F59E0B";
  return "#EF4444";
}

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// Slugs coming back from the API are identifiers, not copy. The ones we
// know about get a translated label; anything else falls back to the
// prettified raw slug so a new backend value still renders legibly.
const STATUS_KEYS: Record<string, DictKey> = {
  active: "field.active",
  suspended: "padmin.users.suspended",
  deleted: "padmin.users.deleted",
};

const TX_TYPE_KEYS: Record<string, DictKey> = {
  topup: "padmin.users.txTopup",
  top_up: "padmin.users.txTopup",
  usage: "padmin.users.txUsage",
  refund: "padmin.users.txRefund",
  adjustment: "padmin.users.txAdjustment",
  admin_adjustment: "padmin.users.txAdjustment",
  trial_grant: "padmin.users.txTrialGrant",
};

function prettifySlug(slug: string): string {
  return slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function planLabel(plan: string, t: Translate): string {
  if (plan === "free_trial") return t("padmin.users.freeTrial");
  return prettifySlug(plan);
}

function StatusBadge({ status }: { status: string }) {
  const t = useT();
  const cls: Record<string, string> = {
    active: "sran-badge-success",
    suspended: "sran-badge-warning",
    deleted: "sran-badge-error",
  };
  const key = STATUS_KEYS[status];
  return (
    <span className={`sran-badge ${cls[status] ?? "sran-badge-neutral"}`}>
      {key ? t(key) : status}
    </span>
  );
}

function PlanChip({ plan }: { plan: string }) {
  const t = useT();
  const color = plan === "free_trial" ? "#64748b" : "var(--accent-primary)";
  return (
    <span style={{ fontSize: 12, fontWeight: 500, color }}>
      {planLabel(plan, t)}
    </span>
  );
}

export default function PortalUsersPage() {
  const t = useT();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "200", offset: "0" });
    if (search.trim()) params.set("search", search.trim());
    if (statusFilter === "active" || statusFilter === "suspended") {
      params.set("status", statusFilter);
    }
    return `/admin/portal-users?${params.toString()}`;
  }, [search, statusFilter]);

  const { data, loading, error, refetch } = useApi<UsersResp>(query);
  const { toast } = useToast();

  const allUsers = data?.users ?? [];

  // Client-side filter for plan-based tabs the backend doesn't support.
  const filtered = useMemo(() => {
    if (statusFilter === "free_trial") {
      return allUsers.filter((u) => u.plan === "free_trial");
    }
    if (statusFilter === "paid") {
      return allUsers.filter((u) => u.plan !== "free_trial");
    }
    return allUsers;
  }, [allUsers, statusFilter]);

  const stats = useMemo(() => {
    const total = allUsers.length;
    const active = allUsers.filter((u) => u.status === "active").length;
    const suspended = allUsers.filter((u) => u.status === "suspended").length;
    const freeTrial = allUsers.filter((u) => u.plan === "free_trial").length;
    return { total, active, suspended, freeTrial };
  }, [allUsers]);

  // ------- modals -------
  const [detailUser, setDetailUser] = useState<PortalUser | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [topupUser, setTopupUser] = useState<PortalUser | null>(null);
  const [topupAmount, setTopupAmount] = useState("");
  const [topupDesc, setTopupDesc] = useState("");
  const [topupSaving, setTopupSaving] = useState(false);

  const [suspendTarget, setSuspendTarget] = useState<PortalUser | null>(null);
  const [suspendWorking, setSuspendWorking] = useState(false);

  async function openDetail(u: PortalUser) {
    setDetailUser(u);
    setDetail(null);
    setDetailLoading(true);
    try {
      const d = await api.get(`/admin/portal-users/${u.id}`);
      setDetail(d as UserDetail);
    } catch (e) {
      toast(t("padmin.users.detailLoadFailed"), "error");
    } finally {
      setDetailLoading(false);
    }
  }

  function closeDetail() {
    setDetailUser(null);
    setDetail(null);
  }

  function openTopup(u: PortalUser) {
    setTopupUser(u);
    setTopupAmount("");
    setTopupDesc("");
  }

  async function handleTopup() {
    if (!topupUser) return;
    const amount = parseInt(topupAmount, 10);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast(t("padmin.users.amountInvalid"), "error");
      return;
    }
    setTopupSaving(true);
    try {
      await api.post(`/admin/portal-users/${topupUser.id}/topup`, {
        amount,
        description: topupDesc.trim() || undefined,
      });
      toast(t("padmin.users.topupSuccess", { amount: amount.toLocaleString() }));
      setTopupUser(null);
      refetch();
      if (detailUser?.id === topupUser.id) openDetail(topupUser);
    } catch (e) {
      const msg = e instanceof ApiError ? e.body?.error?.message : null;
      toast(msg || t("padmin.users.topupFailed"), "error");
    } finally {
      setTopupSaving(false);
    }
  }

  async function toggleStatus(u: PortalUser) {
    // Optimistic: flip the badge immediately, roll back on error.
    const next = u.status === "active" ? "suspended" : "active";
    setSuspendWorking(true);
    try {
      const path = next === "suspended"
        ? `/admin/portal-users/${u.id}/suspend`
        : `/admin/portal-users/${u.id}/activate`;
      await api.post(path, {});
      toast(next === "suspended" ? t("padmin.users.suspendedToast") : t("padmin.users.activatedToast"));
      setSuspendTarget(null);
      refetch();
    } catch (e) {
      const msg = e instanceof ApiError ? e.body?.error?.message : null;
      toast(msg || t("padmin.users.statusUpdateFailed"), "error");
    } finally {
      setSuspendWorking(false);
    }
  }

  const quickAmounts = [100000, 500000, 1000000];

  return (
    <div className="p-4 sm:p-6 lg:p-10" style={{ maxWidth: 1400, margin: "0 auto" }}>
      <div className="sran-page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">{t("padmin.users.title")}</h1>
          <p className="sran-page-subtitle">
            {t("padmin.users.subtitle")}
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4" style={{ gap: 14, marginBottom: 20 }}>
        <StatCard label={t("padmin.users.statTotal")} value={stats.total} trend="active" />
        <StatCard label={t("field.active")} value={stats.active} />
        <StatCard label={t("padmin.users.suspended")} value={stats.suspended} />
        <StatCard label={t("padmin.users.freeTrial")} value={stats.freeTrial} />
      </div>

      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 18px", borderBottom: "1px solid var(--color-border-default)", flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "0 1 320px", minWidth: 180 }}>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("padmin.users.searchPlaceholder")}
              className="sran-input"
              style={{ width: "100%", paddingLeft: 34 }}
            />
            <svg
              width="14" height="14" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}
            >
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
          </div>

          <span style={{ width: 1, height: 24, background: "var(--border-default)", flexShrink: 0 }} />

          <div style={{ display: "flex", gap: 4 }}>
            {([
              { k: "all",         label: t("field.all"),                count: allUsers.length,       dot: null },
              { k: "active",      label: t("field.active"),             count: stats.active,          dot: "#10b981" },
              { k: "suspended",   label: t("padmin.users.suspended"),   count: stats.suspended,       dot: "#f59e0b" },
              { k: "free_trial",  label: t("padmin.users.freeTrial"),   count: stats.freeTrial,       dot: "#64748b" },
              { k: "paid",        label: t("padmin.users.paid"),        count: allUsers.length - stats.freeTrial, dot: "#1e3a5f" },
            ] as { k: StatusFilter; label: string; count: number; dot: string | null }[]).map((f) => {
              const active = statusFilter === f.k;
              return (
                <button
                  key={f.k}
                  onClick={() => setStatusFilter(f.k)}
                  aria-pressed={active}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    // Active state uses a fixed dark-navy fill, so the text
                    // colour must be a fixed white in both themes — the
                    // theme-aware --text-primary lands at #1A1C23 in light
                    // mode and disappears against the navy background
                    // (~1.5:1 contrast). Inactive tabs use --text-primary
                    // directly so they stay legible on either theme's
                    // surface.
                    border: active ? "1px solid #1E3A5F" : "1px solid var(--border-default)",
                    background: active ? "#1E3A5F" : "transparent",
                    color: active ? "#FFFFFF" : "var(--text-primary)",
                    fontSize: 12,
                    fontWeight: active ? 600 : 500,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    transition: "all 120ms",
                  }}
                  onMouseEnter={(e) => {
                    if (!active) {
                      e.currentTarget.style.background = "var(--bg-elevated)";
                      e.currentTarget.style.borderColor = "var(--border-strong, var(--border-default))";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!active) {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.borderColor = "var(--border-default)";
                    }
                  }}
                >
                  {f.dot ? (
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: f.dot, display: active ? "none" : "inline-block" }} />
                  ) : null}
                  {f.label}
                  <span
                    style={{
                      fontWeight: 700,
                      fontSize: 10,
                      color: active ? "#FFFFFF" : "var(--text-tertiary)",
                      opacity: active ? 1 : 0.95,
                    }}
                  >
                    {f.count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Body */}
        {loading ? (
          <div style={{ padding: 20 }}>
            <SkeletonTable rows={6} />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Icon name="users" size={22} />}
            message={t("padmin.users.empty")}
          />
        ) : (
          <div className="overflow-x-auto">
          <table className="sran-table">
            <thead>
              <tr>
                <th style={{ paddingLeft: 20 }}>{t("field.user")}</th>
                <th>{t("padmin.users.plan")}</th>
                <th style={{ width: 200 }}>{t("padmin.users.credits")}</th>
                <th>{t("padmin.users.usage")}</th>
                <th style={{ textAlign: "center", width: 110 }}>{t("field.status")}</th>
                <th style={{ width: 120 }}>{t("field.created")}</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => {
                const pct = creditPercent(u.credit_balance, u.credit_used);
                const color = creditColor(pct, u.credit_balance);
                const initial = (u.display_name || u.email || "?").charAt(0).toUpperCase();
                return (
                  <tr key={u.id} style={{ cursor: "pointer" }} onClick={() => openDetail(u)}>
                    <td style={{ paddingLeft: 20 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                        <div style={{
                          width: 34, height: 34, borderRadius: 6,
                          background: "linear-gradient(135deg, #3b6cf6 0%, #1e3a5f 100%)",
                          color: "white", display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: 13, fontWeight: 700, flexShrink: 0,
                        }}>
                          {initial}
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {u.display_name || "—"}
                          </div>
                          <div style={{ fontSize: 12, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {u.email}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td><PlanChip plan={u.plan} /></td>
                    <td>
                      {u.credit_balance <= 0 ? (
                        <span className="sran-badge sran-badge-error">{t("padmin.users.noCredits")}</span>
                      ) : (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ width: 60, height: 6, borderRadius: 3, background: "var(--bg-elevated)", overflow: "hidden", flexShrink: 0 }}>
                            <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 250ms" }} />
                          </div>
                          <span style={{ fontSize: 12, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                            {formatTokensCompact(u.credit_balance)}
                          </span>
                        </div>
                      )}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
                      {t("padmin.users.usedAmount", { amount: formatTokensCompact(u.credit_used) })}
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <StatusBadge status={u.status} />
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{formatDate(u.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <RowActions
                        user={u}
                        onTopup={() => openTopup(u)}
                        onToggle={() => setSuspendTarget(u)}
                        onView={() => openDetail(u)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      <Modal
        open={!!detailUser}
        onClose={closeDetail}
        title={t("padmin.users.detailTitle")}
        footer={
          <button className="sran-btn sran-btn-secondary" onClick={closeDetail}>
            {t("action.close")}
          </button>
        }
      >
        {detailUser ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ width: 44, height: 44, borderRadius: 8, background: "linear-gradient(135deg, #3b6cf6 0%, #1e3a5f 100%)", color: "white", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, fontWeight: 700 }}>
                {(detailUser.display_name || detailUser.email).charAt(0).toUpperCase()}
              </div>
              <div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{detailUser.display_name || "—"}</div>
                <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>{detailUser.email}</div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem 1rem" }}>
              <DetailRow label={t("padmin.users.plan")} value={planLabel(detailUser.plan, t)} />
              <DetailRow label={t("field.status")} value={<StatusBadge status={detailUser.status} />} />
              <DetailRow label={t("padmin.users.joined")} value={formatDate(detailUser.created_at)} />
              <DetailRow label={t("padmin.users.userId")} value={<code style={{ fontSize: 11, fontFamily: "'JetBrains Mono',monospace" }}>{detailUser.id.slice(0, 8)}…</code>} />
            </div>

            <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "0.75rem" }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)", marginBottom: 8 }}>{t("padmin.users.credits")}</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("padmin.users.balance")}</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatTokensFull(detailUser.credit_balance)}</div></div>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("padmin.users.used")}</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatTokensFull(detailUser.credit_used)}</div></div>
              </div>
              <div style={{ marginTop: 8, height: 6, borderRadius: 3, background: "var(--bg-elevated)", overflow: "hidden" }}>
                <div style={{
                  width: `${creditPercent(detailUser.credit_balance, detailUser.credit_used)}%`,
                  height: "100%",
                  background: creditColor(creditPercent(detailUser.credit_balance, detailUser.credit_used), detailUser.credit_balance),
                }} />
              </div>
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <button className="sran-btn sran-btn-primary" onClick={() => openTopup(detailUser)}>
                <Icon name="dollar" size={14} /> {t("padmin.users.topup")}
              </button>
              <button
                className="sran-btn sran-btn-secondary"
                onClick={() => setSuspendTarget(detailUser)}
              >
                {detailUser.status === "active"
                  ? (<><Icon name="pause" size={14} /> {t("padmin.users.suspend")}</>)
                  : (<><Icon name="play" size={14} /> {t("padmin.users.activate")}</>)}
              </button>
            </div>

            <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "0.75rem" }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)", marginBottom: 8 }}>{t("padmin.users.recentTransactions")}</div>
              {detailLoading ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("state.loading")}</div>
              ) : detail && detail.transactions.length > 0 ? (
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-tertiary)" }}>
                      <th style={{ padding: "4px 8px 4px 0", fontWeight: 500 }}>{t("padmin.users.txDate")}</th>
                      <th style={{ padding: "4px 8px", fontWeight: 500 }}>{t("field.type")}</th>
                      <th style={{ padding: "4px 8px", fontWeight: 500, textAlign: "right" }}>{t("padmin.users.amount")}</th>
                      <th style={{ padding: "4px 0 4px 8px", fontWeight: 500, textAlign: "right" }}>{t("padmin.users.balance")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.transactions.map((tx) => {
                      const typeKey = TX_TYPE_KEYS[tx.type];
                      return (
                      <tr key={tx.id} style={{ borderTop: "1px solid var(--border-default)" }}>
                        <td style={{ padding: "6px 8px 6px 0", color: "var(--text-tertiary)" }}>{formatDate(tx.created_at)}</td>
                        <td style={{ padding: "6px 8px" }}>{typeKey ? t(typeKey) : prettifySlug(tx.type)}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: tx.amount >= 0 ? "#10b981" : "var(--text-primary)" }}>
                          {tx.amount >= 0 ? "+" : ""}{formatTokensFull(tx.amount)}
                        </td>
                        <td style={{ padding: "6px 0 6px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {formatTokensFull(tx.balance_after)}
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("padmin.users.noTransactions")}</div>
              )}
            </div>
          </div>
        ) : null}
      </Modal>

      {/* Top-up modal */}
      <Modal
        open={!!topupUser}
        onClose={() => !topupSaving && setTopupUser(null)}
        title={t("padmin.users.topup")}
        footer={
          <>
            <button
              className="sran-btn sran-btn-secondary"
              onClick={() => setTopupUser(null)}
              disabled={topupSaving}
            >
              {t("action.cancel")}
            </button>
            <button
              className="sran-btn sran-btn-primary"
              onClick={handleTopup}
              disabled={topupSaving}
            >
              {topupSaving ? t("padmin.users.processing") : t("padmin.users.confirmTopup")}
            </button>
          </>
        }
      >
        {topupUser ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              {t("field.user")}: <strong>{topupUser.display_name || topupUser.email}</strong>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
                {t("padmin.users.currentBalance")}: <strong style={{ color: "var(--text-primary)" }}>{formatTokensFull(topupUser.credit_balance)}</strong> {t("padmin.tokensUnit")}
              </div>
            </div>

            <div>
              <label className="sran-label">{t("padmin.users.amountTokens")}</label>
              <input
                type="number"
                min={1}
                className="sran-input"
                placeholder={t("padmin.users.amountPlaceholder")}
                value={topupAmount}
                onChange={(e) => setTopupAmount(e.target.value)}
                autoFocus
              />
              <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                {quickAmounts.map((a) => (
                  <button
                    key={a}
                    type="button"
                    className="sran-btn sran-btn-secondary sran-btn-sm"
                    onClick={() => setTopupAmount(String(a))}
                  >
                    +{formatTokensCompact(a)}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="sran-label">{t("padmin.users.descriptionOptional")}</label>
              <input
                type="text"
                className="sran-input"
                placeholder={t("padmin.users.descPlaceholder")}
                value={topupDesc}
                onChange={(e) => setTopupDesc(e.target.value)}
                maxLength={500}
              />
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmModal
        open={!!suspendTarget}
        onClose={() => !suspendWorking && setSuspendTarget(null)}
        onConfirm={() => suspendTarget && toggleStatus(suspendTarget)}
        title={suspendTarget?.status === "active" ? t("padmin.users.suspendTitle") : t("padmin.users.activateTitle")}
        message={
          suspendTarget?.status === "active"
            ? t("padmin.users.suspendBody", {
                name: suspendTarget?.display_name || suspendTarget?.email || "",
              })
            : t("padmin.users.activateBody", {
                name: suspendTarget?.display_name || suspendTarget?.email || "",
              })
        }
        confirmLabel={suspendTarget?.status === "active" ? t("padmin.users.suspend") : t("padmin.users.activate")}
        loading={suspendWorking}
      />
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 13, color: "var(--text-primary)", marginTop: 2 }}>{value}</div>
    </div>
  );
}

function RowActions({
  user,
  onTopup,
  onToggle,
  onView,
}: {
  user: PortalUser;
  onTopup: () => void;
  onToggle: () => void;
  onView: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative", display: "flex", justifyContent: "flex-end", paddingRight: 12 }}>
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        title={t("field.actions")}
        style={{
          background: "none",
          border: "1px solid transparent",
          width: 26,
          height: 26,
          borderRadius: 6,
          cursor: "pointer",
          color: "var(--text-tertiary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 16,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--bg-elevated)";
          e.currentTarget.style.borderColor = "var(--border-default)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "none";
          e.currentTarget.style.borderColor = "transparent";
        }}
      >
        ⋯
      </button>
      {open ? (
        <div
          onMouseDown={(e) => e.preventDefault()}
          style={{
            position: "absolute",
            right: 8,
            top: 30,
            background: "var(--bg-surface)",
            border: "1px solid var(--color-border-default)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            minWidth: 200,
            padding: 4,
            zIndex: 5,
          }}
        >
          <ActionItem icon={<Icon name="dollar" size={14} />} label={t("padmin.users.topup")} onClick={() => { setOpen(false); onTopup(); }} />
          <ActionItem
            icon={user.status === "active" ? <Icon name="pause" size={14} /> : <Icon name="play" size={14} />}
            label={user.status === "active" ? t("padmin.users.suspend") : t("padmin.users.activate")}
            onClick={() => { setOpen(false); onToggle(); }}
          />
          <ActionItem icon={<Icon name="eye" size={14} />} label={t("action.viewDetails")} onClick={() => { setOpen(false); onView(); }} />
        </div>
      ) : null}
    </div>
  );
}

function ActionItem({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "8px 10px",
        background: "none",
        border: "none",
        borderRadius: 5,
        cursor: "pointer",
        fontSize: 13,
        color: "var(--text-secondary)",
        textAlign: "left",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-elevated)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
    >
      <span style={{ width: 16, textAlign: "center" }}>{icon}</span>
      {label}
    </button>
  );
}
