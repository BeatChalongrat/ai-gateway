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
import { ThemeToggle } from "../../components/ThemeToggle";

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

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string }> = {
    active: { cls: "sran-badge-success", label: "Active" },
    suspended: { cls: "sran-badge-warning", label: "Suspended" },
    deleted: { cls: "sran-badge-error", label: "Deleted" },
  };
  const m = map[status] ?? { cls: "sran-badge-neutral", label: status };
  return <span className={`sran-badge ${m.cls}`}>{m.label}</span>;
}

function PlanChip({ plan }: { plan: string }) {
  const pretty = plan.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const color = plan === "free_trial" ? "#64748b" : "#1e3a5f";
  return (
    <span style={{ fontSize: 12, fontWeight: 500, color }}>
      {pretty}
    </span>
  );
}

export default function PortalUsersPage() {
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
      toast("Failed to load user details", "error");
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
      toast("Amount must be a positive number", "error");
      return;
    }
    setTopupSaving(true);
    try {
      await api.post(`/admin/portal-users/${topupUser.id}/topup`, {
        amount,
        description: topupDesc.trim() || undefined,
      });
      toast(`Topped up ${amount.toLocaleString()} tokens`);
      setTopupUser(null);
      refetch();
      if (detailUser?.id === topupUser.id) openDetail(topupUser);
    } catch (e) {
      const msg = e instanceof ApiError ? e.body?.error?.message : "Top-up failed";
      toast(msg || "Top-up failed", "error");
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
      toast(next === "suspended" ? "User suspended" : "User activated");
      setSuspendTarget(null);
      refetch();
    } catch (e) {
      const msg = e instanceof ApiError ? e.body?.error?.message : "Failed to update status";
      toast(msg || "Failed to update status", "error");
    } finally {
      setSuspendWorking(false);
    }
  }

  const quickAmounts = [100000, 500000, 1000000];

  return (
    <div style={{ padding: "2rem 2.5rem", maxWidth: 1400, margin: "0 auto" }}>
      <div className="sran-page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">Portal Users</h1>
          <p className="sran-page-subtitle">
            Manage portal accounts, credit balances, and access status.
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14, marginBottom: 20 }}>
        <StatCard label="Total Users" value={stats.total} trend="active" />
        <StatCard label="Active" value={stats.active} />
        <StatCard label="Suspended" value={stats.suspended} />
        <StatCard label="Free Trial" value={stats.freeTrial} />
      </div>

      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 18px", borderBottom: "1px solid var(--color-border-default)", flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: "0 1 320px", minWidth: 180 }}>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by email or name..."
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

          <span style={{ width: 1, height: 24, background: "#e8eaef", flexShrink: 0 }} />

          <div style={{ display: "flex", gap: 4 }}>
            {([
              { k: "all",         label: "All",         count: allUsers.length,       dot: null },
              { k: "active",      label: "Active",      count: stats.active,          dot: "#10b981" },
              { k: "suspended",   label: "Suspended",   count: stats.suspended,       dot: "#f59e0b" },
              { k: "free_trial",  label: "Free Trial",  count: stats.freeTrial,       dot: "#64748b" },
              { k: "paid",        label: "Paid",        count: allUsers.length - stats.freeTrial, dot: "#1e3a5f" },
            ] as { k: StatusFilter; label: string; count: number; dot: string | null }[]).map((f) => {
              const active = statusFilter === f.k;
              return (
                <button
                  key={f.k}
                  onClick={() => setStatusFilter(f.k)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    border: "1px solid transparent",
                    background: active ? "#1E3A5F" : "transparent",
                    color: active ? "var(--text-primary)" : "var(--text-secondary)",
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    transition: "all 120ms",
                  }}
                >
                  {f.dot ? (
                    <span style={{ width: 7, height: 7, borderRadius: "50%", background: f.dot, display: active ? "none" : "inline-block" }} />
                  ) : null}
                  {f.label}
                  <span style={{ fontWeight: 700, fontSize: 10, opacity: active ? 0.9 : 0.6 }}>
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
            icon="👥"
            message="No portal users yet. Share your signup link to get started."
          />
        ) : (
          <table className="sran-table">
            <thead>
              <tr>
                <th style={{ paddingLeft: 20 }}>User</th>
                <th>Plan</th>
                <th style={{ width: 200 }}>Credits</th>
                <th>Usage</th>
                <th style={{ textAlign: "center", width: 110 }}>Status</th>
                <th style={{ width: 120 }}>Created</th>
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
                        <span className="sran-badge sran-badge-error">No credits</span>
                      ) : (
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ width: 60, height: 6, borderRadius: 3, background: "#eef0f4", overflow: "hidden", flexShrink: 0 }}>
                            <div style={{ width: `${pct}%`, height: "100%", background: color, transition: "width 250ms" }} />
                          </div>
                          <span style={{ fontSize: 12, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                            {formatTokensCompact(u.credit_balance)}
                          </span>
                        </div>
                      )}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
                      {formatTokensCompact(u.credit_used)} used
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
        )}
      </div>

      {/* Detail drawer */}
      <Modal
        open={!!detailUser}
        onClose={closeDetail}
        title="User Detail"
        footer={
          <button className="sran-btn sran-btn-secondary" onClick={closeDetail}>
            Close
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
              <DetailRow label="Plan" value={detailUser.plan.replace(/_/g, " ")} />
              <DetailRow label="Status" value={<StatusBadge status={detailUser.status} />} />
              <DetailRow label="Joined" value={formatDate(detailUser.created_at)} />
              <DetailRow label="User ID" value={<code style={{ fontSize: 11, fontFamily: "'JetBrains Mono',monospace" }}>{detailUser.id.slice(0, 8)}…</code>} />
            </div>

            <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "0.75rem" }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)", marginBottom: 8 }}>Credits</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>Balance</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatTokensFull(detailUser.credit_balance)}</div></div>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>Used</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatTokensFull(detailUser.credit_used)}</div></div>
              </div>
              <div style={{ marginTop: 8, height: 6, borderRadius: 3, background: "#eef0f4", overflow: "hidden" }}>
                <div style={{
                  width: `${creditPercent(detailUser.credit_balance, detailUser.credit_used)}%`,
                  height: "100%",
                  background: creditColor(creditPercent(detailUser.credit_balance, detailUser.credit_used), detailUser.credit_balance),
                }} />
              </div>
            </div>

            <div style={{ display: "flex", gap: 8 }}>
              <button className="sran-btn sran-btn-primary" onClick={() => openTopup(detailUser)}>
                💰 Top-up Credits
              </button>
              <button
                className="sran-btn sran-btn-secondary"
                onClick={() => setSuspendTarget(detailUser)}
              >
                {detailUser.status === "active" ? "⏸ Suspend" : "▶ Activate"}
              </button>
            </div>

            <div style={{ borderTop: "1px solid var(--color-border-default)", paddingTop: "0.75rem" }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)", marginBottom: 8 }}>Recent Transactions</div>
              {detailLoading ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</div>
              ) : detail && detail.transactions.length > 0 ? (
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-tertiary)" }}>
                      <th style={{ padding: "4px 8px 4px 0", fontWeight: 500 }}>Date</th>
                      <th style={{ padding: "4px 8px", fontWeight: 500 }}>Type</th>
                      <th style={{ padding: "4px 8px", fontWeight: 500, textAlign: "right" }}>Amount</th>
                      <th style={{ padding: "4px 0 4px 8px", fontWeight: 500, textAlign: "right" }}>Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.transactions.map((t) => (
                      <tr key={t.id} style={{ borderTop: "1px solid #f0f2f5" }}>
                        <td style={{ padding: "6px 8px 6px 0", color: "var(--text-tertiary)" }}>{formatDate(t.created_at)}</td>
                        <td style={{ padding: "6px 8px" }}>{t.type.replace(/_/g, " ")}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums", color: t.amount >= 0 ? "#10b981" : "#111827" }}>
                          {t.amount >= 0 ? "+" : ""}{formatTokensFull(t.amount)}
                        </td>
                        <td style={{ padding: "6px 0 6px 8px", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                          {formatTokensFull(t.balance_after)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>No transactions yet.</div>
              )}
            </div>
          </div>
        ) : null}
      </Modal>

      {/* Top-up modal */}
      <Modal
        open={!!topupUser}
        onClose={() => !topupSaving && setTopupUser(null)}
        title="Top-up Credits"
        footer={
          <>
            <button
              className="sran-btn sran-btn-secondary"
              onClick={() => setTopupUser(null)}
              disabled={topupSaving}
            >
              Cancel
            </button>
            <button
              className="sran-btn sran-btn-primary"
              onClick={handleTopup}
              disabled={topupSaving}
            >
              {topupSaving ? "Processing…" : "Confirm Top-up"}
            </button>
          </>
        }
      >
        {topupUser ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              User: <strong>{topupUser.display_name || topupUser.email}</strong>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
                Current balance: <strong style={{ color: "var(--text-primary)" }}>{formatTokensFull(topupUser.credit_balance)}</strong> tokens
              </div>
            </div>

            <div>
              <label className="sran-label">Amount (tokens)</label>
              <input
                type="number"
                min={1}
                className="sran-input"
                placeholder="e.g. 100000"
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
              <label className="sran-label">Description (optional)</label>
              <input
                type="text"
                className="sran-input"
                placeholder="e.g. Promotional credit"
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
        title={suspendTarget?.status === "active" ? "Suspend user?" : "Activate user?"}
        message={
          suspendTarget?.status === "active"
            ? `Suspend ${suspendTarget?.display_name || suspendTarget?.email}? They will immediately lose gateway access.`
            : `Reactivate ${suspendTarget?.display_name || suspendTarget?.email}? They will regain gateway access.`
        }
        confirmLabel={suspendTarget?.status === "active" ? "Suspend" : "Activate"}
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
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative", display: "flex", justifyContent: "flex-end", paddingRight: 12 }}>
      <button
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        title="Actions"
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
          <ActionItem icon="💰" label="Top-up Credits" onClick={() => { setOpen(false); onTopup(); }} />
          <ActionItem
            icon={user.status === "active" ? "⏸" : "▶"}
            label={user.status === "active" ? "Suspend" : "Activate"}
            onClick={() => { setOpen(false); onToggle(); }}
          />
          <ActionItem icon="👁" label="View Details" onClick={() => { setOpen(false); onView(); }} />
        </div>
      ) : null}
    </div>
  );
}

function ActionItem({ icon, label, onClick }: { icon: string; label: string; onClick: () => void }) {
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
