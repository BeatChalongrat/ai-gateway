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
import { balanceAfterCeiling, roomToCeiling } from "../../lib/credits";

type Translate = ReturnType<typeof useT>;

// FIX88 Part A — a user's credits are a balance filled TOWARDS a stored
// ceiling (credit_quota). Three actions move them, all posted to the
// ledger by the backend: top up (capped at the ceiling; "fill to full" is
// the obvious one), reset to full (balance := ceiling), and change the
// ceiling (the balance moves by the same difference, floored at 0). Each
// works on one user from the row menu / detail drawer, or on an explicit
// tick-box selection through one confirm that names the count and the
// total credits moved.
interface PortalUser {
  id: string;
  email: string;
  display_name: string | null;
  plan: string;
  status: string;
  credit_balance: number;
  credit_used: number;
  credit_quota: number;
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

interface BulkResult {
  results: { user_id: string; ok: boolean; error?: string; message?: string; balance_after: number; quota_after: number; moved: number }[];
  total_moved: number;
  reduced_count: number;
}

type StatusFilter = "all" | "active" | "suspended" | "free_trial" | "paid";
type CreditAction = "topup" | "reset" | "quota";

function formatTokensCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return n.toLocaleString();
}

function formatTokensFull(n: number): string {
  return n.toLocaleString();
}

/** Percent of the CEILING still available. A zero ceiling reads as empty. */
function creditPercent(balance: number, quota: number): number {
  if (quota <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((balance / quota) * 100)));
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
  manual_topup: "padmin.users.txTopup",
  usage: "padmin.users.txUsage",
  refund: "padmin.users.txRefund",
  adjustment: "padmin.users.txAdjustment",
  admin_adjustment: "padmin.users.txAdjustment",
  trial_grant: "padmin.users.txTrialGrant",
  signup_bonus: "padmin.users.txTrialGrant",
  reset_to_full: "padmin.users.txReset",
  purchase: "padmin.users.txPurchase",
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

  // ------- selection (explicit tick boxes; never "everyone I cannot see") -------
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const selected = useMemo(() => allUsers.filter((u) => selectedIds.has(u.id)), [allUsers, selectedIds]);
  const allVisibleSelected = filtered.length > 0 && filtered.every((u) => selectedIds.has(u.id));
  function toggleRow(id: string) {
    setSelectedIds((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  function toggleAllVisible() {
    setSelectedIds((s) => {
      const n = new Set(s);
      if (allVisibleSelected) filtered.forEach((u) => n.delete(u.id));
      else filtered.forEach((u) => n.add(u.id));
      return n;
    });
  }

  // ------- modals -------
  const [detailUser, setDetailUser] = useState<PortalUser | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // One credit modal for the three actions; `targets` is one user (row
  // menu / drawer) or the selection (bulk bar).
  const [action, setAction] = useState<CreditAction | null>(null);
  const [targets, setTargets] = useState<PortalUser[]>([]);
  const [amountText, setAmountText] = useState("");
  const [fill, setFill] = useState(false);
  const [quotaText, setQuotaText] = useState("");
  const [desc, setDesc] = useState("");
  const [saving, setSaving] = useState(false);
  // The final confirm: every bulk action, and any single-user reduction.
  const [confirmOpen, setConfirmOpen] = useState(false);

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

  function openAction(a: CreditAction, users: PortalUser[]) {
    setAction(a);
    setTargets(users);
    setAmountText("");
    setFill(false);
    setQuotaText(users.length === 1 ? String(users[0]!.credit_quota) : "");
    setDesc("");
    setConfirmOpen(false);
  }
  function closeAction() {
    if (saving) return;
    setAction(null);
    setTargets([]);
    setConfirmOpen(false);
  }

  const isBulk = targets.length > 1;
  const one = targets.length === 1 ? targets[0]! : null;
  const amount = parseInt(amountText, 10);
  const newQuota = parseInt(quotaText, 10);
  const room = (u: PortalUser) => roomToCeiling(u.credit_balance, u.credit_quota);

  // ------- previews (what the confirm names) -------
  const preview = useMemo(() => {
    if (!action) return null;
    if (action === "topup") {
      if (fill) {
        const total = targets.reduce((s, u) => s + room(u), 0);
        const full = targets.filter((u) => room(u) === 0).length;
        return { total, full, skipped: 0, reduced: 0 };
      }
      if (!Number.isFinite(amount) || amount <= 0) return null;
      const eligible = targets.filter((u) => amount <= room(u));
      return { total: amount * eligible.length, full: 0, skipped: targets.length - eligible.length, reduced: 0 };
    }
    if (action === "reset") {
      const total = targets.reduce((s, u) => s + room(u), 0);
      const full = targets.filter((u) => room(u) === 0).length;
      return { total, full, skipped: 0, reduced: 0 };
    }
    // quota
    if (!Number.isFinite(newQuota) || newQuota < 0) return null;
    let total = 0;
    let reduced = 0;
    for (const u of targets) {
      const moved = balanceAfterCeiling(u.credit_balance, u.credit_quota, newQuota) - u.credit_balance;
      total += moved;
      if (newQuota < u.credit_quota) reduced++;
    }
    return { total, full: 0, skipped: 0, reduced };
  }, [action, targets, fill, amount, newQuota]);

  const singleOverCeiling = one !== null && action === "topup" && !fill && Number.isFinite(amount) && amount > room(one);
  const singleLowering = one !== null && action === "quota" && Number.isFinite(newQuota) && newQuota < one.credit_quota;

  const canApply = (() => {
    if (!action || targets.length === 0) return false;
    if (action === "topup") return fill ? targets.some((u) => room(u) > 0) : Number.isFinite(amount) && amount > 0 && !singleOverCeiling;
    if (action === "reset") return targets.some((u) => room(u) > 0);
    return Number.isFinite(newQuota) && newQuota >= 0 && (isBulk || newQuota !== one!.credit_quota);
  })();

  /** The primary button: bulk and any single reduction go through the
   *  ConfirmModal (FIX84's rule — name the user, the consequence); the
   *  ordinary single-user add applies at once. */
  function onPrimary() {
    if (!canApply) return;
    if (isBulk || singleLowering) {
      setConfirmOpen(true);
      return;
    }
    void apply();
  }

  async function apply() {
    if (!action || targets.length === 0) return;
    setSaving(true);
    try {
      const description = desc.trim() || undefined;
      if (isBulk) {
        const body: Record<string, unknown> = { user_ids: targets.map((u) => u.id), description };
        if (action === "topup") {
          body.action = fill ? "fill" : "topup";
          if (!fill) body.amount = amount;
        } else if (action === "reset") {
          body.action = "reset";
        } else {
          body.action = "quota";
          body.quota = newQuota;
        }
        const res = (await api.post(`/admin/portal-users/bulk`, body)) as BulkResult;
        const ok = res.results.filter((r) => r.ok).length;
        const failed = res.results.length - ok;
        const vars = { ok: String(ok), n: String(res.results.length), failed: String(failed), total: res.total_moved.toLocaleString() };
        toast(failed > 0 ? t("padmin.users.bulkPartial", vars) : t("padmin.users.bulkDone", vars), failed > 0 ? "error" : undefined);
        setSelectedIds(new Set());
      } else if (one) {
        if (action === "topup") {
          if (fill && room(one) === 0) {
            toast(t("padmin.users.resetNoop", { name: one.display_name || one.email }));
          } else {
            await api.post(`/admin/portal-users/${one.id}/topup`, fill ? { fill: true, description } : { amount, description });
            toast(t("padmin.users.topupSuccess", { amount: (fill ? room(one) : amount).toLocaleString() }));
          }
        } else if (action === "reset") {
          const res = (await api.post(`/admin/portal-users/${one.id}/reset`, { description })) as { transaction: Transaction | null };
          if (res.transaction) toast(t("padmin.users.resetSuccess", { delta: res.transaction.amount.toLocaleString() }));
          else toast(t("padmin.users.resetNoop", { name: one.display_name || one.email }));
        } else {
          await api.post(`/admin/portal-users/${one.id}/quota`, { quota: newQuota, description });
          toast(t("padmin.users.ceilingSuccess", { quota: newQuota.toLocaleString() }));
        }
      }
      const reopen = one && detailUser?.id === one.id ? one : null;
      setAction(null);
      setTargets([]);
      setConfirmOpen(false);
      refetch();
      if (reopen) openDetail(reopen);
    } catch (e) {
      const msg = e instanceof ApiError ? e.body?.error?.message : null;
      toast(msg || (isBulk ? t("padmin.users.bulkFailed") : t("padmin.users.topupFailed")), "error");
      setConfirmOpen(false);
    } finally {
      setSaving(false);
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

  const nameOf = (u: PortalUser) => u.display_name || u.email;

  // The confirm text: bulk names the count and the total credits (and the
  // reductions separately); a single reduction names the user, the new
  // ceiling and the new balance.
  const confirmText = (() => {
    if (!action || !preview) return "";
    if (!isBulk && one && action === "quota") {
      const nb = balanceAfterCeiling(one.credit_balance, one.credit_quota, newQuota);
      return t("padmin.users.ceilingLowerBody", {
        name: nameOf(one), oldQuota: formatTokensFull(one.credit_quota), newQuota: formatTokensFull(newQuota),
        oldBalance: formatTokensFull(one.credit_balance), newBalance: formatTokensFull(nb), removed: formatTokensFull(one.credit_balance - nb),
      });
    }
    const n = String(targets.length);
    if (action === "topup" && !fill) return t("padmin.users.bulkTopupBody", { n, amount: formatTokensFull(amount), total: formatTokensFull(preview.total), skipped: String(preview.skipped) });
    if (action === "topup") return t("padmin.users.bulkFillBody", { n, total: formatTokensFull(preview.total), full: String(preview.full) });
    if (action === "reset") return t("padmin.users.bulkResetBody", { n, total: formatTokensFull(preview.total), full: String(preview.full) });
    return t("padmin.users.bulkQuotaBody", { n, quota: formatTokensFull(newQuota), total: formatTokensFull(preview.total), reduced: String(preview.reduced) });
  })();

  const actionTitle = action === "topup" ? t("padmin.users.topup") : action === "reset" ? t("padmin.users.reset") : t("padmin.users.changeCeiling");

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

        {/* FIX88 — bulk bar: only for an explicit selection */}
        {selected.length > 0 ? (
          <div
            data-testid="bulk-bar"
            style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 18px", borderBottom: "1px solid var(--color-border-default)", background: "var(--bg-elevated)", flexWrap: "wrap" }}
          >
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }} data-testid="bulk-count">
              {t("padmin.users.selected", { n: String(selected.length) })}
            </span>
            <button className="sran-btn sran-btn-primary sran-btn-sm" onClick={() => openAction("topup", selected)} data-testid="bulk-topup">
              <Icon name="dollar" size={13} /> {t("padmin.users.topup")}
            </button>
            <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => openAction("reset", selected)} data-testid="bulk-reset">
              {t("padmin.users.reset")}
            </button>
            <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => openAction("quota", selected)} data-testid="bulk-quota">
              {t("padmin.users.changeCeiling")}
            </button>
            <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => setSelectedIds(new Set())} style={{ marginLeft: "auto" }} data-testid="bulk-clear">
              {t("padmin.users.clearSelection")}
            </button>
          </div>
        ) : null}

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
                <th style={{ width: 36, paddingLeft: 20 }}>
                  <input
                    type="checkbox"
                    aria-label={t("padmin.users.selectAllVisible")}
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    data-testid="select-all"
                  />
                </th>
                <th>{t("field.user")}</th>
                <th>{t("padmin.users.plan")}</th>
                <th style={{ width: 220 }}>{t("padmin.users.credits")}</th>
                <th>{t("padmin.users.usage")}</th>
                <th style={{ textAlign: "center", width: 110 }}>{t("field.status")}</th>
                <th style={{ width: 120 }}>{t("field.created")}</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => {
                const pct = creditPercent(u.credit_balance, u.credit_quota);
                const color = creditColor(pct, u.credit_balance);
                const initial = (u.display_name || u.email || "?").charAt(0).toUpperCase();
                const checked = selectedIds.has(u.id);
                return (
                  <tr key={u.id} style={{ cursor: "pointer" }} onClick={() => openDetail(u)} data-testid="user-row" data-user-id={u.id} aria-selected={checked}>
                    <td style={{ paddingLeft: 20 }} onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={t("padmin.users.selectRow", { name: nameOf(u) })}
                        checked={checked}
                        onChange={() => toggleRow(u.id)}
                        data-testid="select-row"
                      />
                    </td>
                    <td>
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
                          <span style={{ fontSize: 12, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }} data-testid="row-credits">
                            {formatTokensCompact(u.credit_balance)}
                            <span style={{ color: "var(--text-tertiary)" }}> / {formatTokensCompact(u.credit_quota)}</span>
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
                        onTopup={() => openAction("topup", [u])}
                        onReset={() => openAction("reset", [u])}
                        onQuota={() => openAction("quota", [u])}
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
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("padmin.users.balance")}</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }} data-testid="detail-balance">{formatTokensFull(detailUser.credit_balance)}</div></div>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("padmin.users.ceiling")}</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }} data-testid="detail-quota">{formatTokensFull(detailUser.credit_quota)}</div></div>
                <div><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("padmin.users.used")}</div><div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{formatTokensFull(detailUser.credit_used)}</div></div>
              </div>
              <div style={{ marginTop: 8, height: 6, borderRadius: 3, background: "var(--bg-elevated)", overflow: "hidden" }}>
                <div style={{
                  width: `${creditPercent(detailUser.credit_balance, detailUser.credit_quota)}%`,
                  height: "100%",
                  background: creditColor(creditPercent(detailUser.credit_balance, detailUser.credit_quota), detailUser.credit_balance),
                }} />
              </div>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="sran-btn sran-btn-primary" onClick={() => openAction("topup", [detailUser])}>
                <Icon name="dollar" size={14} /> {t("padmin.users.topup")}
              </button>
              <button className="sran-btn sran-btn-secondary" onClick={() => openAction("reset", [detailUser])}>
                {t("padmin.users.reset")}
              </button>
              <button className="sran-btn sran-btn-secondary" onClick={() => openAction("quota", [detailUser])}>
                {t("padmin.users.changeCeiling")}
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

      {/* FIX88 — the credit action modal (top up / reset / ceiling; one user or the selection) */}
      <Modal
        open={action !== null && !confirmOpen}
        onClose={closeAction}
        title={actionTitle}
        footer={
          <>
            <button
              className="sran-btn sran-btn-secondary"
              onClick={closeAction}
              disabled={saving}
            >
              {t("action.cancel")}
            </button>
            <button
              className="sran-btn sran-btn-primary"
              onClick={onPrimary}
              disabled={saving || !canApply}
              data-testid="credit-action-primary"
            >
              {saving
                ? t("padmin.users.processing")
                : isBulk
                  ? t("padmin.users.bulkConfirm", { n: String(targets.length) })
                  : action === "topup"
                    ? t("padmin.users.confirmTopup")
                    : action === "reset"
                      ? t("padmin.users.reset")
                      : singleLowering
                        ? t("padmin.users.lowerCeiling")
                        : t("padmin.users.changeCeiling")}
            </button>
          </>
        }
      >
        {action ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }} data-testid="credit-action-modal" data-action={action} data-bulk={isBulk ? "true" : "false"}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              {t("field.user")}: <strong>{one ? nameOf(one) : t("padmin.users.forUsers", { n: String(targets.length) })}</strong>
              {one ? (
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }} data-testid="credit-action-figures">
                  {t("padmin.users.currentBalance")}: <strong style={{ color: "var(--text-primary)" }}>{formatTokensFull(one.credit_balance)}</strong>{" "}
                  {t("padmin.users.ofCeiling", { quota: formatTokensFull(one.credit_quota) })}
                  {action === "topup" ? (
                    <> · {t("padmin.users.roomLeft")}: <strong style={{ color: "var(--text-primary)" }}>{formatTokensFull(room(one))}</strong></>
                  ) : null}
                </div>
              ) : null}
            </div>

            {action === "topup" ? (
              <div>
                <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className={`sran-btn sran-btn-sm ${fill ? "sran-btn-primary" : "sran-btn-secondary"}`}
                    onClick={() => setFill((v) => !v)}
                    aria-pressed={fill}
                    disabled={!!one && room(one) === 0}
                    data-testid="fill-to-full"
                  >
                    {one && room(one) === 0
                      ? t("padmin.users.alreadyFull")
                      : t("padmin.users.fillToFull", { room: one ? formatTokensCompact(room(one)) : formatTokensCompact(preview?.total ?? 0) })}
                  </button>
                </div>
                {!fill ? (
                  <>
                    <label className="sran-label">{t("padmin.users.amountTokens")}</label>
                    <input
                      type="number"
                      min={1}
                      className="sran-input"
                      placeholder={t("padmin.users.amountPlaceholder")}
                      value={amountText}
                      onChange={(e) => setAmountText(e.target.value)}
                      autoFocus
                      data-testid="amount-input"
                    />
                    <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                      {quickAmounts.map((a) => (
                        <button
                          key={a}
                          type="button"
                          className="sran-btn sran-btn-secondary sran-btn-sm"
                          onClick={() => setAmountText(String(a))}
                        >
                          +{formatTokensCompact(a)}
                        </button>
                      ))}
                    </div>
                    {singleOverCeiling && one ? (
                      <div style={{ marginTop: 8, fontSize: 12, color: "var(--error-text, #EF4444)" }} role="alert" data-testid="over-ceiling">
                        {t("padmin.users.overCeiling", { over: formatTokensFull(amount - room(one)), room: formatTokensFull(room(one)) })}
                      </div>
                    ) : null}
                  </>
                ) : null}
              </div>
            ) : null}

            {action === "reset" && one ? (
              <div style={{ fontSize: 13, color: "var(--text-secondary)" }} data-testid="reset-preview">
                {room(one) === 0
                  ? t("padmin.users.alreadyFull")
                  : t("padmin.users.resetBody", { name: nameOf(one), balance: formatTokensFull(one.credit_balance), quota: formatTokensFull(one.credit_quota), delta: formatTokensFull(room(one)) })}
              </div>
            ) : null}

            {action === "quota" ? (
              <div>
                <label className="sran-label">{t("padmin.users.newCeiling")}</label>
                <input
                  type="number"
                  min={0}
                  className="sran-input"
                  value={quotaText}
                  onChange={(e) => setQuotaText(e.target.value)}
                  autoFocus
                  data-testid="quota-input"
                />
                {one && Number.isFinite(newQuota) && newQuota >= 0 ? (
                  <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }} data-testid="quota-preview">
                    {t("padmin.users.ceilingPreview", {
                      oldQuota: formatTokensFull(one.credit_quota), newQuota: formatTokensFull(newQuota),
                      oldBalance: formatTokensFull(one.credit_balance), newBalance: formatTokensFull(balanceAfterCeiling(one.credit_balance, one.credit_quota, newQuota)),
                    })}
                  </div>
                ) : null}
                {(singleLowering || (isBulk && (preview?.reduced ?? 0) > 0)) ? (
                  <div style={{ marginTop: 8, fontSize: 12, color: "var(--error-text, #EF4444)" }} role="alert" data-testid="lower-warning">
                    {t("padmin.users.ceilingLowerWarn")}
                  </div>
                ) : null}
              </div>
            ) : null}

            {isBulk && preview ? (
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }} data-testid="bulk-preview">{confirmText}</div>
            ) : null}

            <div>
              <label className="sran-label">{t("padmin.users.descriptionOptional")}</label>
              <input
                type="text"
                className="sran-input"
                placeholder={t("padmin.users.descPlaceholder")}
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                maxLength={500}
              />
            </div>
          </div>
        ) : null}
      </Modal>

      {/* FIX88 — the one confirm for every bulk action and any single reduction */}
      <ConfirmModal
        open={confirmOpen}
        onClose={() => !saving && setConfirmOpen(false)}
        onConfirm={() => void apply()}
        title={isBulk ? t("padmin.users.bulkTitle", { n: String(targets.length) }) : t("padmin.users.ceilingLowerTitle")}
        message={confirmText}
        confirmLabel={isBulk ? t("padmin.users.bulkConfirm", { n: String(targets.length) }) : t("padmin.users.lowerCeiling")}
        loading={saving}
      />

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
  onReset,
  onQuota,
  onToggle,
  onView,
}: {
  user: PortalUser;
  onTopup: () => void;
  onReset: () => void;
  onQuota: () => void;
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
        aria-label={t("field.actions")}
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
          <ActionItem icon={<Icon name="play" size={14} />} label={t("padmin.users.reset")} onClick={() => { setOpen(false); onReset(); }} />
          <ActionItem icon={<Icon name="settings" size={14} />} label={t("padmin.users.changeCeiling")} onClick={() => { setOpen(false); onQuota(); }} />
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
