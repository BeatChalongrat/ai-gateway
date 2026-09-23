"use client";

// Floating usage toggle (Style A — mini ring pill) + usage panel.
//
// The pill floats at the bottom-right of the chat area, just above the
// composer: a 22px ring showing % credits remaining (green > 50%,
// amber 20–50%, red < 20%), the remaining balance, and a chevron that
// flips while the panel is open. The panel opens UPWARD from the pill
// with a slide-up fade. Click-outside (backdrop) and Escape both
// close it.
//
// FIX86 — two rules this file now keeps:
//  * "above the composer" is a RELATIONSHIP (`useBottomInset`), not the
//    literal 76 px it used to be; the badge moves when the composer does.
//  * ONE number means "used". The ring and the footer both read the
//    account ledger (`credits.used`, what actually left the balance —
//    the same field the Portal's USED shows). The per-model bars come
//    from the request-log roll-up (tenant-wide, priced from logged
//    cost) and are labelled as such; their total is shown under its own
//    name, never as "used".
//  * The badge does not vanish when the gateway cannot answer: it says
//    the number is unavailable and offers a retry (FIX77: collapse,
//    never delete).

import { useEffect, useRef, useState } from "react";

import { ChevronUp, ExternalLink } from "lucide-react";

import { BADGE_GAP, useAboveComposer, useBottomInset } from "@/hooks/useBottomInset";
import { useUsage } from "@/hooks/useUsage";
import { PORTAL_URL } from "@/lib/auth";
import { tOutside, useT } from "@/lib/i18n";
import type { ModelUsage } from "@/lib/types";
import { useComposerStore } from "@/stores/composerStore";
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

/** The pill's rendered height (22 px ring + py-1.5 + border) — used only
 *  where nothing is laid out (jsdom). */
export const PILL_FALLBACK_HEIGHT = 36;

function levelColor(pctRemaining: number): string {
  if (pctRemaining > 50) return LEVEL.ok;
  if (pctRemaining > 20) return LEVEL.warn;
  return LEVEL.danger;
}

export function CreditBadge() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const { data, isError } = useUsage();

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
  const inset = useBottomInset();
  const above = useAboveComposer();
  // FIX130 C4 — tell the thread how far up the badge reaches, so it keeps
  // that much room under its last message (the badge covered the last
  // timestamp at 360 px, NIGHT2 F11 / FIX128 §6).
  const setBadge = useComposerStore((s) => s.setBadge);
  const pillRef = useRef<HTMLButtonElement>(null);
  const shown = Boolean(data) || isError;
  useEffect(() => {
    if (!shown) return;
    const h = pillRef.current?.getBoundingClientRect().height || PILL_FALLBACK_HEIGHT;
    setBadge(h + BADGE_GAP);
    return () => setBadge(0);
  }, [shown, setBadge]);
  // Nothing yet and nothing wrong: the first fetch is in flight — no
  // pill flashes a placeholder. A failed fetch keeps the pill and says so.
  if (!shown) return null;
  const unavailable = !data;

  const { total, remaining } = data?.credits ?? { total: 0, remaining: 0 };
  const pctRemaining = total > 0 ? Math.round((remaining / total) * 100) : 100;
  const color = unavailable ? "var(--text-tertiary)" : levelColor(pctRemaining);

  return (
    // FIX76 — stay over the thread, not over a docked side pane.
    // FIX86 — and above the composer's MEASURED height, whatever it is.
    <div
      className="absolute z-10"
      style={{ right: 16 + dockedWidth, bottom: inset + above + BADGE_GAP }}
      data-testid="credit-badge"
      data-unavailable={unavailable ? "true" : undefined}
    >
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
          ref={pillRef}
          type="button"
          aria-label={unavailable ? t("usage.unavailable") : t("a11y.usageAndCredits")}
          title={unavailable ? t("usage.unavailable") : undefined}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="focus-ring relative z-50 inline-flex items-center gap-2 rounded-[20px] border border-border bg-bg-base py-1.5 pl-2 pr-3.5 transition-colors hover:border-border-strong"
        >
          <MiniRing pct={unavailable ? 0 : pctRemaining} color={color} />
          <span className="text-xs font-medium tabular-nums text-text-primary">
            {unavailable ? "—" : remaining.toLocaleString()}
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
  const { data, refetch, isStale, isFetching } = useUsage();
  const models = useModelStore((s) => s.models);

  // Refresh on open when the cached snapshot is older than staleTime
  // (60s) — keeps panel opens cheap but never minutes out of date.
  useEffect(() => {
    if (isStale) void refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) {
    // FIX86 Part C — the number is unavailable; say so, keep the way out.
    return (
      <div
        role="dialog"
        aria-label={t("a11y.usage")}
        data-testid="usage-panel-unavailable"
        className="usage-panel-enter absolute bottom-[calc(100%+8px)] right-0 z-50 w-80 max-w-[calc(100vw-32px)] rounded-xl border border-border bg-bg-surface p-4 shadow-xl"
      >
        <div className="text-sm font-semibold text-text-primary">{t("usage.unavailable")}</div>
        <p className="mt-1 text-xs text-text-muted">{t("usage.unavailableHint")}</p>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="focus-ring mt-3 rounded-full border border-border px-3 py-1 text-xs font-medium text-text-primary transition-colors hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-40"
        >
          {isFetching ? "…" : t("usage.retry")}
        </button>
      </div>
    );
  }

  const { total, remaining, reset_date } = data.credits;
  const pctRemaining = total > 0 ? Math.round((remaining / total) * 100) : 100;
  const ringColor = levelColor(pctRemaining);
  const rows = data.usage_by_model;
  // ONE source for "used": the account ledger — the same number the ring
  // is drawn from and the Portal's USED shows. The request-log roll-up
  // (`total_credits_used`) is a different fact and gets its own label.
  const deducted = data.credits.used;
  const loggedTotal = data.total_credits_used;

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

      {/* Usage by model — from the request-log roll-up, tenant-wide */}
      <div className="px-4 pb-1 pt-3">
        <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-text-muted">
          {t("usage.byModel")}
          <span className="ml-1 normal-case tracking-normal text-text-muted" data-testid="usage-by-model-scope">
            · {t("usage.byModelScope")}
          </span>
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

      {/* Footer — the ledger, i.e. what the ring already says */}
      <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
        <span className="text-xs text-text-muted">{t("usage.deducted")}</span>
        <span className="text-xs font-medium tabular-nums text-text-primary" data-testid="usage-deducted">
          {deducted.toLocaleString()} {t("usage.unit.credits")}
        </span>
      </div>
      {typeof loggedTotal === "number" ? (
        <div
          className="flex items-center justify-between px-4 pb-2.5 text-[11px] text-text-muted"
          title={t("usage.logTotalHint")}
        >
          <span>{t("usage.logTotal")}</span>
          <span className="tabular-nums" data-testid="usage-log-total">
            {loggedTotal.toLocaleString()} {t("usage.unit.credits")}
          </span>
        </div>
      ) : null}
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
