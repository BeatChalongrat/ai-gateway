"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Gift,
  PlusCircle,
  Receipt,
  ShoppingBag,
  Sliders,
  Sparkles,
  Star,
  Zap,
} from "lucide-react";

import { Button } from "@/components/Button";
import { Modal } from "@/components/Modal";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, formatTokens, formatUsd } from "@/lib/format";
import { useCountUp } from "@/lib/hooks/useCountUp";
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

  // Hooks must run on every render — evaluate the count-up before the
  // early return for the loading state. When `user` isn't loaded yet,
  // target is 0 and the hook is a no-op.
  const displayBalance = useCountUp(user?.credit_balance ?? 0, 1000);

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
        <div
          style={{
            background: "rgba(220,38,38,0.08)",
            border: "1px solid rgba(220,38,38,0.25)",
            color: "var(--accent-danger)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      ) : null}

      {purchaseMsg ? (
        <div
          className="flex items-start justify-between gap-3"
          style={{
            background: "rgba(99,102,241,0.08)",
            border: "1px solid rgba(99,102,241,0.25)",
            color: "var(--accent-primary)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          <span>{purchaseMsg}</span>
          <button
            onClick={() => setPurchaseMsg(null)}
            aria-label="Dismiss"
            style={{
              background: "transparent",
              border: "none",
              color: "inherit",
              fontSize: 18,
              lineHeight: 1,
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>
      ) : null}

      {/* Balance hero — same gradient + grid + glow as dashboard */}
      <div
        style={{
          position: "relative",
          overflow: "hidden",
          background: "var(--hero-gradient)",
          border: "1px solid var(--border-default)",
          borderRadius: 16,
          padding: "28px 32px",
          boxShadow: "0 20px 40px -20px var(--hero-glow)",
          color: "#FFFFFF",
        }}
      >
        <div
          aria-hidden
          className="hero-grid"
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage:
              "linear-gradient(rgba(99,102,241,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,0.05) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
            pointerEvents: "none",
          }}
        />
        <div
          aria-hidden
          className="hero-glow"
          style={{
            position: "absolute",
            top: -80,
            right: -80,
            width: 280,
            height: 280,
            background:
              "radial-gradient(circle, rgba(99,102,241,0.25) 0%, transparent 70%)",
            pointerEvents: "none",
          }}
        />
        <div className="relative flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                color: "rgba(255,255,255,0.7)",
              }}
            >
              Current Balance
            </div>
            <p className="mt-3 flex items-baseline gap-2">
              <span
                className="font-heading"
                style={{
                  fontSize: 42,
                  fontWeight: 700,
                  letterSpacing: "-0.02em",
                  lineHeight: 1,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {formatTokens(displayBalance)}
              </span>
              <span style={{ fontSize: 13, color: "rgba(255,255,255,0.6)" }}>tokens</span>
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <span
              className="inline-flex items-center rounded-full"
              style={{
                background: "rgba(99,102,241,0.15)",
                border: "1px solid rgba(99,102,241,0.3)",
                color: "#A5B4FC",
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 500,
                gap: 6,
              }}
            >
              <Sparkles size={11} />
              {planLabel}
            </span>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.55)" }}>
              Used:{" "}
              <span style={{ color: "rgba(255,255,255,0.9)", fontWeight: 500 }}>
                {formatTokens(user?.credit_used ?? 0)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div>
        <h2
          className="font-heading mb-4"
          style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
        >
          Token Packs
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {sorted.map((p) => {
            const popular = p.id === popularId;
            const features = featuresFor(p);
            return (
              <div
                key={p.id}
                className={[
                  "card-hover relative p-6 flex flex-col",
                  popular ? "sm:scale-[1.02]" : "",
                ].join(" ")}
                style={{
                  background: popular
                    ? "linear-gradient(180deg, rgba(99,102,241,0.06), var(--bg-surface-2))"
                    : "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
                  border: popular
                    ? "2px solid var(--accent-primary)"
                    : "1px solid var(--border-default)",
                  borderRadius: 16,
                  boxShadow: popular
                    ? "0 8px 24px -8px rgba(99,102,241,0.25)"
                    : "0 1px 2px rgba(0,0,0,0.03)",
                }}
              >
                {popular ? (
                  <span
                    className="absolute -top-3 left-1/2 -translate-x-1/2 inline-flex items-center gap-1 rounded-full"
                    style={{
                      background:
                        "linear-gradient(90deg, var(--accent-primary), var(--accent-violet))",
                      padding: "4px 12px",
                      fontSize: 10,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.12em",
                      color: "#FFFFFF",
                      boxShadow: "0 4px 12px rgba(99,102,241,0.3)",
                    }}
                  >
                    <Star size={11} strokeWidth={2.5} style={{ fill: "#FFFFFF" }} />
                    Most Popular
                  </span>
                ) : null}

                <h3
                  className="mt-1 text-center font-heading"
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: "var(--text-primary)",
                  }}
                >
                  {p.name}
                </h3>

                <div className="mt-4 text-center">
                  <div
                    className="font-heading leading-none"
                    style={{
                      fontSize: 36,
                      fontWeight: 800,
                      color: "var(--text-primary)",
                    }}
                  >
                    {formatTokens(p.tokens / 1_000_000)}
                    <span
                      style={{
                        fontSize: 16,
                        fontWeight: 400,
                        color: "var(--text-tertiary)",
                      }}
                    >
                      M
                    </span>
                  </div>
                  <div
                    className="mt-1"
                    style={{
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.14em",
                      color: "var(--text-tertiary)",
                    }}
                  >
                    tokens
                  </div>
                </div>

                <div className="mt-5 text-center">
                  <span
                    style={{ fontSize: 13, color: "var(--text-tertiary)", marginRight: 2 }}
                  >
                    $
                  </span>
                  <span
                    className="font-heading"
                    style={{
                      fontSize: 30,
                      fontWeight: 700,
                      color: "var(--text-primary)",
                    }}
                  >
                    {Math.round(p.price_usd)}
                  </span>
                </div>

                <ul className="mt-5 space-y-2">
                  {features.map((f) => (
                    <li
                      key={f}
                      className="flex items-center gap-2"
                      style={{ fontSize: 13, color: "var(--text-secondary)" }}
                    >
                      <Check
                        size={14}
                        strokeWidth={2.25}
                        style={{ color: "var(--accent-success)", flexShrink: 0 }}
                      />
                      {f}
                    </li>
                  ))}
                </ul>

                <div className="mt-6">
                  <Button
                    fullWidth
                    className="btn-premium"
                    onClick={() => setConfirmPack(p)}
                    style={{
                      background: "var(--bg-sidebar)",
                      border: "1px solid var(--bg-sidebar)",
                      color: "#FFFFFF",
                    }}
                  >
                    Purchase
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div
        className="overflow-hidden"
        style={{
          background:
            "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
          border: "1px solid var(--border-default)",
          borderRadius: 12,
        }}
      >
        <div
          className="px-6 py-4"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <h3
            className="font-heading"
            style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}
          >
            Transaction History
          </h3>
          <p style={{ fontSize: 12.5, color: "var(--text-tertiary)", marginTop: 2 }}>
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
            <table className="w-full" style={{ fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border-subtle)",
                    background: "var(--bg-surface-2)",
                  }}
                >
                  <Th>Date</Th>
                  <Th>Type</Th>
                  <Th>Description</Th>
                  <Th align="right">Amount</Th>
                  <Th align="right">Balance</Th>
                </tr>
              </thead>
              <tbody>
                {txs.map((t) => (
                  <tr
                    key={t.id}
                    className="row-hover"
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <Td color="var(--text-secondary)" nowrap>
                      {formatDate(t.created_at)}
                    </Td>
                    <Td>
                      <TxTypeChip type={t.type} />
                    </Td>
                    <Td color="var(--text-secondary)">
                      {t.description ?? t.request_id ?? "—"}
                    </Td>
                    <Td
                      align="right"
                      color={
                        t.amount >= 0
                          ? "var(--accent-success)"
                          : "var(--accent-danger)"
                      }
                      weight={500}
                    >
                      {t.amount >= 0 ? "+" : ""}
                      {formatTokens(t.amount)}
                    </Td>
                    <Td align="right" color="var(--text-primary)">
                      {formatTokens(t.balance_after)}
                    </Td>
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
          <div className="space-y-2">
            <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              You are purchasing{" "}
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                {confirmPack.name}
              </span>{" "}
              — {formatTokens(confirmPack.tokens)} tokens for{" "}
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                {formatUsd(confirmPack.price_usd)}
              </span>
              .
            </p>
            <p style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>
              Payment processing is coming soon. Purchasing now will not charge you.
            </p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        padding: "12px 24px",
        textAlign: align,
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--text-muted)",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  color = "var(--text-primary)",
  align = "left",
  nowrap = false,
  weight,
}: {
  children: React.ReactNode;
  color?: string;
  align?: "left" | "right";
  nowrap?: boolean;
  weight?: number;
}) {
  return (
    <td
      style={{
        padding: "12px 24px",
        color,
        textAlign: align,
        fontSize: 13,
        whiteSpace: nowrap ? "nowrap" : "normal",
        fontWeight: weight,
      }}
    >
      {children}
    </td>
  );
}

type TxType = "signup_bonus" | "usage" | "manual_topup" | "purchase" | "adjustment";

function TxTypeChip({ type }: { type: string }) {
  // Icon + token-mapped color per transaction type. Anything unknown
  // falls back to a neutral pill so new backend types still render.
  const map: Record<
    TxType,
    { label: string; Icon: typeof Zap; bg: string; color: string }
  > = {
    signup_bonus: {
      label: "Signup bonus",
      Icon: Gift,
      bg: "rgba(16,185,129,0.12)",
      color: "var(--accent-success)",
    },
    usage: {
      label: "Usage",
      Icon: Zap,
      bg: "rgba(99,102,241,0.12)",
      color: "var(--accent-primary)",
    },
    manual_topup: {
      label: "Top-up",
      Icon: PlusCircle,
      bg: "rgba(16,185,129,0.12)",
      color: "var(--accent-success)",
    },
    purchase: {
      label: "Purchase",
      Icon: ShoppingBag,
      bg: "rgba(139,92,246,0.14)",
      color: "var(--accent-violet)",
    },
    adjustment: {
      label: "Adjustment",
      Icon: Sliders,
      bg: "rgba(245,158,11,0.14)",
      color: "var(--accent-warning)",
    },
  };
  const m = map[type as TxType];
  if (!m) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5"
        style={{
          fontSize: 11,
          background: "var(--bg-elevated)",
          color: "var(--text-secondary)",
        }}
      >
        {type}
      </span>
    );
  }
  const { Icon, label, bg, color } = m;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5"
      style={{ fontSize: 11, fontWeight: 500, background: bg, color }}
    >
      <Icon size={12} strokeWidth={2} />
      {label}
    </span>
  );
}
