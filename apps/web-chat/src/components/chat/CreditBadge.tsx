"use client";

// Floating usage toggle (Style A — mini ring pill) + usage panel.
//
// The pill floats at the bottom-right of the chat area, just above the
// composer: a 22px ring showing % credits remaining (green > 50%,
// amber 20–50%, red < 20%), the remaining balance, and a chevron that
// flips while the panel is open. The panel opens UPWARD from the pill
// with a slide-up fade. Click-outside (backdrop) and Escape both
// close it. Numbers come from the same proxied portal API the Portal
// Dashboard reads; 1 credit = 1 token, lifetime pool (no reset).

import { useEffect, useState } from "react";

import { ChevronUp, ExternalLink } from "lucide-react";

import { useUsage } from "@/hooks/useUsage";
import { PORTAL_URL } from "@/lib/auth";
import { tOutside, useT } from "@/lib/i18n";
import type { ModelUsage } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";
import { useSidePaneStore } from "@/stores/sidePaneStore";

// Credit-level colours (badge ring + panel ring). Fixed hex — they
// read correctly on both themes.
const LEVEL = {
  ok: "#5DCAA5",
  warn: "#EF9F27",
  danger: "#E24B4A",
} as const;

// Single unified colour for every per-model progress bar.
const BAR_COLOR = "#378ADD";

// Gateway provider ids → display labels. Unknown ids fall back to a
// capitalised first letter.
const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  groq: "Groq",
  xai: "xAI",
  mistral: "Mistral",
  cohere: "Cohere",
  perplexity: "Perplexity",
};

function providerLabel(id: string): string {
  if (!id) return "";
  return PROVIDER_LABELS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

function levelColor(pctRemaining: number): string {
  if (pctRemaining > 50) return LEVEL.ok;
  if (pctRemaining > 20) return LEVEL.warn;
  return LEVEL.danger;
}

export function CreditBadge() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const { data } = useUsage();

  // Escape closes the panel (backdrop handles click-outside).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const dockedWidth = useSidePaneStore((s) => s.dockedWidth);
  if (!data) return null;

  const { total, remaining } = data.credits;
  const pctRemaining = total > 0 ? Math.round((remaining / total) * 100) : 100;
  const color = levelColor(pctRemaining);

  return (
    // FIX76 — stay over the thread, not over a docked side pane.
    <div className="absolute bottom-[76px] z-10" style={{ right: 16 + dockedWidth }}>
      {open ? (
        <div
          className="fixed inset-0 z-40"
          aria-hidden
          onClick={() => setOpen(false)}
        />
      ) : null}

      <div className="relative">
        {open ? <UsagePanel onClose={() => setOpen(false)} /> : null}

        <button
          type="button"
          aria-label={t("a11y.usageAndCredits")}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="focus-ring relative z-50 inline-flex items-center gap-2 rounded-[20px] border border-border bg-bg-base py-1.5 pl-2 pr-3.5 transition-colors hover:border-border-strong"
        >
          <MiniRing pct={pctRemaining} color={color} />
          <span className="text-xs font-medium tabular-nums text-text-primary">
            {remaining.toLocaleString()}
          </span>
          <ChevronUp
            size={12}
            aria-hidden
            className={`text-text-muted transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>
      </div>
    </div>
  );
}

// 22px ring: r=9 → circumference ≈ 56.5. Track at 15% opacity of the
// current text colour so it adapts to both themes.
function MiniRing({ pct, color }: { pct: number; color: string }) {
  const c = 2 * Math.PI * 9;
  const filled = (Math.min(100, Math.max(0, pct)) / 100) * c;
  return (
    <svg
      width={22}
      height={22}
      viewBox="0 0 22 22"
      className="-rotate-90 text-text-primary"
      aria-hidden
    >
      <circle
        cx={11}
        cy={11}
        r={9}
        fill="none"
        stroke="currentColor"
        strokeOpacity={0.15}
        strokeWidth={2.5}
      />
      <circle
        cx={11}
        cy={11}
        r={9}
        fill="none"
        stroke={color}
        strokeWidth={2.5}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${c - filled}`}
      />
    </svg>
  );
}

function UsagePanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const { data, refetch, isStale } = useUsage();
  const models = useModelStore((s) => s.models);

  // Refresh on open when the cached snapshot is older than staleTime
  // (60s) — keeps panel opens cheap but never minutes out of date.
  useEffect(() => {
    if (isStale) void refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) return null;

  const { total, remaining, reset_date } = data.credits;
  const pctRemaining = total > 0 ? Math.round((remaining / total) * 100) : 100;
  const ringColor = levelColor(pctRemaining);
  const rows = data.usage_by_model;
  const totalUsed = data.total_credits_used ?? data.credits.used;

  const displayName = (id: string) =>
    models.find((m) => m.id === id)?.name ?? id;

  return (
    <div
      role="dialog"
      aria-label={t("a11y.usage")}
      className="usage-panel-enter absolute bottom-[calc(100%+8px)] right-0 z-50 max-h-[70vh] w-80 max-w-[calc(100vw-32px)] overflow-y-auto rounded-xl border border-border bg-bg-surface shadow-xl"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <span className="text-sm font-semibold text-text-primary">{t("usage.title")}</span>
        <a
          href={`${PORTAL_URL}/usage`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onClose}
          className="focus-ring flex items-center gap-1 rounded text-xs text-text-secondary transition-colors hover:text-text-primary"
        >
          {t("usage.viewInPortal")}
          <ExternalLink size={11} aria-hidden />
        </a>
      </div>

      {/* Credit summary card */}
      <div className="mx-3 flex items-center gap-4 rounded-lg bg-bg-secondary p-3">
        <RingChart pct={pctRemaining} color={ringColor} />
        <div className="min-w-0">
          <div className="text-lg font-semibold tabular-nums text-text-primary">
            {remaining.toLocaleString()}
          </div>
          <div className="text-xs text-text-muted">
            {t("usage.creditsRemaining", { total: total.toLocaleString() })}
          </div>
          {reset_date ? (
            <div className="mt-0.5 text-xs text-text-muted">
              {t("usage.resets", { date: new Date(reset_date).toLocaleDateString() })}
            </div>
          ) : null}
        </div>
      </div>

      {/* Usage by model */}
      <div className="px-4 pb-1 pt-3">
        <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-text-muted">
          {t("usage.byModel")}
        </div>
        {rows.length === 0 ? (
          <p className="pb-2 text-xs text-text-muted">{t("usage.empty")}</p>
        ) : (
          <div className="flex flex-col gap-2.5 pb-2">
            {rows.map((row) => (
              <ModelRow
                key={row.model}
                row={row}
                name={displayName(row.model)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
        <span className="text-xs text-text-muted">{t("usage.totalUsed")}</span>
        <span className="text-xs font-medium tabular-nums text-text-primary">
          {totalUsed.toLocaleString()} {t("usage.unit.credits")}
        </span>
      </div>
    </div>
  );
}

function ModelRow({ row, name }: { row: ModelUsage; name: string }) {
  const provider = providerLabel(row.provider);
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-text-primary">
          {name}
        </span>
        <span className="shrink-0 text-[11px] font-medium tabular-nums text-text-primary">
          {row.credits_used.toLocaleString()} {tOutside("usage.unit.credits")}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[11px] text-text-muted">
          {provider}
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-text-muted">
          {row.percentage}%
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-elevated">
        <div
          className="h-full rounded-full transition-[width]"
          style={{
            width: `${Math.min(100, Math.max(0, row.percentage))}%`,
            background: BAR_COLOR,
            opacity: 0.8,
          }}
        />
      </div>
    </div>
  );
}

// CSS/SVG-only ring: a track circle plus a progress arc whose dash
// length is pct% of the circumference. Rotated -90° so it starts at
// 12 o'clock.
function RingChart({ pct, color }: { pct: number; color: string }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(100, Math.max(0, pct)) / 100) * c;
  return (
    <div className="relative h-16 w-16 shrink-0" aria-hidden>
      <svg width={64} height={64} viewBox="0 0 64 64" className="-rotate-90">
        <circle
          cx={32}
          cy={32}
          r={r}
          fill="none"
          stroke="var(--border-default)"
          strokeWidth={6}
        />
        <circle
          cx={32}
          cy={32}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${c - filled}`}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold tabular-nums text-text-primary">
        {pct}%
      </span>
    </div>
  );
}
