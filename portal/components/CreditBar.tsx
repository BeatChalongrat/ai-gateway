"use client";

import Link from "next/link";
import { ArrowRight, ArrowUpRight, Sparkles } from "lucide-react";

interface Props {
  balance: number;
  used: number;
  plan: string;
  onUpgrade?: () => void;
  /** When set, renders a small "View transactions →" link in the
   *  bar's footer. Used only on the dashboard; /billing already has
   *  the transaction table inline. */
  viewTransactionsHref?: string;
  /** Optional overrides — if omitted, total defaults to balance+used,
   *  resetLabel to the plan's implied cadence, and sparkline to zeros. */
  total?: number;
  resetLabel?: string;
  sparkline?: number[];
  planLabel?: string;
}

const DEFAULT_SPARKLINE = [0, 0, 0, 0, 0, 0, 0];

function formatNum(n: number): string {
  return n.toLocaleString("en-US");
}

function humanizePlan(plan: string): string {
  return plan.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function CreditBar({
  balance,
  used,
  plan,
  onUpgrade,
  viewTransactionsHref,
  total,
  resetLabel = "— no cap —",
  sparkline = DEFAULT_SPARKLINE,
  planLabel,
}: Props) {
  const effectiveTotal = total ?? balance + used;
  const percent =
    effectiveTotal > 0 ? Math.round((balance / effectiveTotal) * 100) : 0;

  const circumference = 2 * Math.PI * 42; // r = 42
  const dashOffset = circumference * (1 - percent / 100);

  const label = planLabel ?? humanizePlan(plan);
  const maxSpark = Math.max(0.0001, ...sparkline);

  return (
    <div className="relative overflow-hidden rounded-2xl bg-[#0A1428] border border-white/5 shadow-xl text-white">
      {/* Aurora mesh — 3 soft colored blobs layered on top of the navy. */}
      <div className="pointer-events-none absolute inset-0 opacity-80">
        <div className="absolute -top-24 -left-24 w-96 h-96 rounded-full bg-violet-500/25 blur-3xl" />
        <div className="absolute -bottom-32 -right-16 w-[28rem] h-[28rem] rounded-full bg-cyan-400/15 blur-3xl" />
        <div className="absolute top-1/2 left-1/3 w-72 h-72 -translate-y-1/2 rounded-full bg-indigo-500/10 blur-3xl" />
      </div>

      {/* Subtle grid texture */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,1) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />

      <div className="relative p-7 md:p-8">
        {/* Top row — label + plan pill */}
        <div className="flex items-center justify-between mb-6">
          <div className="text-sm font-bold uppercase tracking-[0.16em] text-white">
            Available Credits
          </div>
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/10 border border-white/15 backdrop-blur-sm text-xs font-medium">
            <Sparkles className="w-3 h-3 text-violet-300" />
            {label}
          </div>
        </div>

        {/* Main row — balance + donut */}
        <div className="flex items-start justify-between gap-8 mb-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-5xl md:text-6xl font-heading font-bold tracking-tight leading-none">
                {formatNum(balance)}
              </span>
              <span className="text-sm text-white/60">tokens</span>
            </div>
            <div className="text-sm text-white/50 mt-2">
              of {formatNum(effectiveTotal)} total
            </div>

            {/* 7-day sparkline */}
            <div className="flex items-end gap-1 mt-5 h-8">
              {sparkline.map((v, i) => {
                const h = v > 0 ? (v / maxSpark) * 100 : 8;
                const isDim = v === 0;
                return (
                  <div
                    key={i}
                    className={`w-2 rounded-sm transition-all ${
                      isDim ? "bg-white/10" : "bg-violet-400"
                    }`}
                    style={{ height: `${h}%` }}
                  />
                );
              })}
              <span className="ml-3 text-xs text-white/40 self-center">
                Last 7 days
              </span>
            </div>
          </div>

          {/* Donut progress */}
          <div className="relative flex-shrink-0 w-[108px] h-[108px]">
            <svg
              viewBox="0 0 100 100"
              className="w-full h-full -rotate-90"
              aria-hidden="true"
            >
              <circle
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke="rgba(255,255,255,0.08)"
                strokeWidth="7"
              />
              <circle
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke="url(#creditDonutGradient)"
                strokeWidth="7"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                style={{ transition: "stroke-dashoffset 1s ease-out" }}
              />
              <defs>
                <linearGradient
                  id="creditDonutGradient"
                  x1="0%"
                  y1="0%"
                  x2="100%"
                  y2="100%"
                >
                  <stop offset="0%" stopColor="#8B7FFF" />
                  <stop offset="100%" stopColor="#3DB5C7" />
                </linearGradient>
              </defs>
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="text-2xl font-heading font-bold tracking-tight">
                {percent}%
              </div>
              <div className="text-[10px] uppercase tracking-widest text-white/50 mt-0.5">
                Available
              </div>
            </div>
          </div>
        </div>

        {/* Divider */}
        <div className="h-px bg-white/10 mb-5" />

        {/* Bottom row — 3 stats + upgrade */}
        <div className="flex items-end justify-between gap-6 flex-wrap">
          <div className="flex items-stretch gap-6 flex-1 min-w-0">
            <Stat label="Used" value={formatNum(used)} hint="tokens" />
            <div className="w-px bg-white/10 self-stretch" />
            <Stat label="Remaining" value={formatNum(balance)} hint="tokens" />
            <div className="w-px bg-white/10 self-stretch" />
            <Stat label="Next reset" value={resetLabel} hint="" />
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            {viewTransactionsHref ? (
              <Link
                href={viewTransactionsHref}
                className="inline-flex items-center gap-1 text-xs text-white/70 hover:text-white transition-colors whitespace-nowrap"
              >
                View transactions <ArrowRight className="w-3 h-3" />
              </Link>
            ) : null}
            {onUpgrade ? (
              <button
                type="button"
                onClick={onUpgrade}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-white text-gray-900 text-sm font-semibold hover:bg-white/90 transition-colors shadow-sm"
              >
                Upgrade
                <ArrowUpRight className="w-3.5 h-3.5" />
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-widest text-white/50 mb-1">
        {label}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-base font-semibold text-white truncate">{value}</span>
        {hint ? <span className="text-xs text-white/40">{hint}</span> : null}
      </div>
    </div>
  );
}
