"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Gift,
  PlusCircle,
  Receipt,
  ShoppingBag,
  Sliders,
  Star,
  Zap,
} from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Modal } from "@/components/Modal";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, formatTokens, formatUsd } from "@/lib/format";
import type { CreditTransaction, PortalUser, TokenPack } from "@/lib/types";
import EmptyState from "../_components/EmptyState";
import PageHeader from "../_components/PageHeader";

interface TransactionsResp {
  transactions: CreditTransaction[];
  total: number;
}

const PACK_FEATURES: Record<string, string[]> = {
  Starter: ["All models", "No expiry", "Email support"],
  Pro: ["All models", "No expiry", "Priority support"],
  Business: ["All models", "No expiry", "Dedicated support"],
};

function featuresFor(pack: TokenPack): string[] {
  return PACK_FEATURES[pack.name] ?? ["All models", "No expiry", "Email support"];
}

export default function BillingPage() {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [packs, setPacks] = useState<TokenPack[]>([]);
  const [txs, setTxs] = useState<CreditTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [confirmPack, setConfirmPack] = useState<TokenPack | null>(null);
  const [purchaseMsg, setPurchaseMsg] = useState<string | null>(null);
  const [purchasing, setPurchasing] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [me, p, t] = await Promise.all([
        api.get<PortalUser>("/portal/me"),
        api.get<TokenPack[]>("/portal/billing/packs"),
        api.get<TransactionsResp>("/portal/me/transactions?limit=20"),
      ]);
      setUser(me);
      setPacks(p);
      setTxs(t.transactions);
    } catch (err) {
      if (err instanceof PortalApiError) setError(err.message);
      else setError("Failed to load billing info.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Sort by sort_order, and find the "popular" pack (sort_order === 2
  // matches the seeded convention). Surfaced in a distinct visual state.
  const sorted = useMemo(
    () => [...packs].sort((a, b) => a.sort_order - b.sort_order),
    [packs]
  );
  const popularId = useMemo(() => {
    const p = sorted.find((x) => x.sort_order === 2);
    return p?.id ?? null;
  }, [sorted]);

  async function handlePurchase() {
    if (!confirmPack) return;
    setPurchasing(true);
    try {
      await api.post("/portal/billing/purchase", { pack_id: confirmPack.id });
      setPurchaseMsg("Purchase successful.");
      setConfirmPack(null);
      load();
    } catch (err) {
      if (err instanceof PortalApiError) {
        setPurchaseMsg(err.message);
      } else {
        setPurchaseMsg("Something went wrong.");
      }
      setConfirmPack(null);
    } finally {
      setPurchasing(false);
    }
  }

  if (loading) return <PageSpinner />;

  const planLabel =
    user?.plan.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) ?? "—";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Billing & Credits"
        subtitle="Top up your balance and review past transactions."
      />

      {error ? (
        <div className="rounded-lg bg-red-50 border border-red-200 text-brand-red text-sm px-3 py-2">
          {error}
        </div>
      ) : null}

      {purchaseMsg ? (
        <div className="rounded-lg bg-blue-50 border border-blue-200 text-blue-800 text-sm px-3 py-2 flex items-start justify-between gap-3">
          <span>{purchaseMsg}</span>
          <button
            onClick={() => setPurchaseMsg(null)}
            className="text-blue-900 text-lg leading-none"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ) : null}

      {/* Balance — gradient card matching /dashboard CreditBar */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-brand-dark via-brand-navy to-brand-dark p-7 text-white shadow-lg">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-20 -right-20 h-56 w-56 rounded-full opacity-30"
          style={{
            background:
              "radial-gradient(circle, rgba(59,108,246,0.5), transparent 70%)",
          }}
        />
        <div className="relative flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-[0.2em] text-white/60">
              Current Balance
            </div>
            <p className="mt-3 flex items-baseline gap-2">
              <span className="font-heading font-extrabold leading-none tracking-tight text-[42px]">
                {formatTokens(user?.credit_balance ?? 0)}
              </span>
              <span className="text-sm text-white/60">tokens</span>
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <span className="inline-flex items-center rounded-full bg-brand-blue/20 border border-brand-blue/40 px-2.5 py-0.5 text-[11px] font-medium text-white">
              {planLabel}
            </span>
            <div className="text-right text-xs text-white/60">
              Used: <span className="text-white/90 font-medium">{formatTokens(user?.credit_used ?? 0)}</span>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-heading font-semibold mb-4">Token Packs</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {sorted.map((p) => {
            const popular = p.id === popularId;
            const features = featuresFor(p);
            return (
              <div
                key={p.id}
                className={[
                  "relative rounded-2xl bg-white border shadow-sm p-6 flex flex-col card-hover",
                  popular
                    ? "border-2 border-brand-blue shadow-md sm:scale-[1.02]"
                    : "border-gray-200",
                ].join(" ")}
              >
                {popular ? (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-brand-navy to-brand-blue px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-white shadow">
                    <Star size={11} strokeWidth={2.5} className="fill-white" />
                    Most Popular
                  </span>
                ) : null}

                <h3 className="mt-1 text-center text-xl font-heading font-bold text-brand-navy">
                  {p.name}
                </h3>

                <div className="mt-4 text-center">
                  <div className="font-heading font-extrabold text-4xl text-gray-900 leading-none">
                    {formatTokens(p.tokens / 1_000_000)}
                    <span className="text-base font-normal text-gray-500">M</span>
                  </div>
                  <div className="mt-1 text-xs uppercase tracking-widest text-gray-500">
                    tokens
                  </div>
                </div>

                <div className="mt-5 text-center">
                  <span className="text-sm text-gray-500 mr-0.5">$</span>
                  <span className="font-heading font-bold text-3xl text-gray-900">
                    {Math.round(p.price_usd)}
                  </span>
                </div>

                <ul className="mt-5 space-y-2">
                  {features.map((f) => (
                    <li key={f} className="flex items-center gap-2 text-sm text-gray-700">
                      <Check size={14} strokeWidth={2.25} className="text-brand-green shrink-0" />
                      {f}
                    </li>
                  ))}
                </ul>

                <div className="mt-6">
                  <Button
                    fullWidth
                    variant={popular ? "primary" : "secondary"}
                    onClick={() => setConfirmPack(p)}
                  >
                    Purchase
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h3 className="font-heading font-semibold text-gray-900">
            Transaction History
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            Purchases, bonuses, and usage charges
          </p>
        </div>
        {txs.length === 0 ? (
          <EmptyState
            icon={<Receipt className="w-7 h-7" strokeWidth={1.5} />}
            title="No transactions yet"
            description="When you purchase a token pack, your receipts will appear here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-100">
                <tr className="text-left text-xs uppercase tracking-wider text-gray-500">
                  <th className="px-6 py-3 font-medium">Date</th>
                  <th className="px-6 py-3 font-medium">Type</th>
                  <th className="px-6 py-3 font-medium">Description</th>
                  <th className="px-6 py-3 font-medium text-right">Amount</th>
                  <th className="px-6 py-3 font-medium text-right">Balance</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {txs.map((t) => (
                  <tr key={t.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-3 text-gray-600 whitespace-nowrap">
                      {formatDate(t.created_at)}
                    </td>
                    <td className="px-6 py-3">
                      <TxTypeChip type={t.type} />
                    </td>
                    <td className="px-6 py-3 text-gray-700">
                      {t.description ?? t.request_id ?? "—"}
                    </td>
                    <td
                      className={[
                        "px-6 py-3 text-right font-medium",
                        t.amount >= 0 ? "text-green-700" : "text-rose-700",
                      ].join(" ")}
                    >
                      {t.amount >= 0 ? "+" : ""}
                      {formatTokens(t.amount)}
                    </td>
                    <td className="px-6 py-3 text-right text-gray-900">
                      {formatTokens(t.balance_after)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={!!confirmPack}
        onClose={() => !purchasing && setConfirmPack(null)}
        title="Confirm purchase"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setConfirmPack(null)}
              disabled={purchasing}
            >
              Cancel
            </Button>
            <Button onClick={handlePurchase} loading={purchasing}>
              Purchase
            </Button>
          </>
        }
      >
        {confirmPack ? (
          <div className="text-sm text-gray-700 space-y-2">
            <p>
              You are purchasing{" "}
              <span className="font-semibold">{confirmPack.name}</span> —{" "}
              {formatTokens(confirmPack.tokens)} tokens for{" "}
              <span className="font-semibold">{formatUsd(confirmPack.price_usd)}</span>.
            </p>
            <p className="text-xs text-gray-500">
              Payment processing is coming soon. Purchasing now will not charge you.
            </p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

type TxType = "signup_bonus" | "usage" | "manual_topup" | "purchase" | "adjustment";

function TxTypeChip({ type }: { type: string }) {
  // Icon + color per transaction type. Anything unknown falls back to a
  // neutral pill with the raw type string so new backend types still
  // render sensibly.
  const map: Record<TxType, { label: string; Icon: typeof Zap; bg: string; text: string }> = {
    signup_bonus: { label: "Signup bonus", Icon: Gift, bg: "bg-green-50", text: "text-green-700" },
    usage: { label: "Usage", Icon: Zap, bg: "bg-blue-50", text: "text-blue-700" },
    manual_topup: { label: "Top-up", Icon: PlusCircle, bg: "bg-green-50", text: "text-green-700" },
    purchase: { label: "Purchase", Icon: ShoppingBag, bg: "bg-purple-50", text: "text-purple-700" },
    adjustment: { label: "Adjustment", Icon: Sliders, bg: "bg-amber-50", text: "text-amber-700" },
  };
  const m = map[type as TxType];
  if (!m) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs bg-gray-100 text-gray-700">
        {type}
      </span>
    );
  }
  const { Icon, label, bg, text } = m;
  return (
    <span
      className={["inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium", bg, text].join(" ")}
    >
      <Icon size={12} strokeWidth={2} />
      {label}
    </span>
  );
}
