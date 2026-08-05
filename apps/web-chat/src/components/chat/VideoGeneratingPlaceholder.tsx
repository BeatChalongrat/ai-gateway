"use client";

// Loading placeholder shown while a video is generating.
//
// Two modes, driven entirely by optional props so existing call sites stay
// untouched:
//   - No props (the tool path / SSE `video_generating` in MessageBubble):
//     the original indeterminate theme-aware shimmer, 320x180 (16:9).
//   - With props (Sprint 3 direct-dispatch form): a determinate progress
//     bar, a live ETA, and a Cancel button.
//
// CSS-only base animation reuses the global .skeleton shimmer + Tailwind's
// animate-pulse. Mirrors ImageGeneratingPlaceholder.

import { useEffect, useState } from "react";

import { Video } from "lucide-react";
import { useT } from "@/lib/i18n";

interface Props {
  /** Determinate progress (0–100). null/undefined → indeterminate shimmer. */
  progressPct?: number | null;
  /** Poll start time; with `estimatedDurationSec` drives the ETA line. */
  startedAt?: Date;
  /** Heuristic total generation time (seconds) for the ETA fallback. */
  estimatedDurationSec?: number;
  /** When provided, a Cancel button is shown (direct-dispatch path). */
  onCancel?: () => void;
}

export function VideoGeneratingPlaceholder({
  progressPct,
  startedAt,
  estimatedDurationSec,
  onCancel,
}: Props) {
  const t = useT();
  // 1s tick to keep the ETA live; only runs once we have a start time.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!startedAt) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  const hasProgress = typeof progressPct === "number" && progressPct >= 0;

  let etaText: string | null = null;
  if (startedAt && estimatedDurationSec) {
    const elapsed = (Date.now() - startedAt.getTime()) / 1000;
    // Progress-based estimate when we have a %, else the static heuristic.
    const remaining =
      hasProgress && progressPct! > 0
        ? (elapsed / progressPct!) * (100 - progressPct!)
        : Math.max(0, estimatedDurationSec - elapsed);
    etaText =
      remaining > 0
        ? t("gen.eta", { s: String(Math.ceil(remaining)) })
        : t("gen.finishing");
  }

  return (
    <div
      className="relative mt-1 h-[180px] w-[320px] max-w-full overflow-hidden rounded-lg border border-border bg-bg-secondary"
      role="status"
      aria-label={t("gen.video")}
    >
      {/* Indeterminate shimmer only when there's no determinate progress. */}
      {!hasProgress ? (
        <div className="skeleton absolute inset-0 rounded-none" aria-hidden />
      ) : null}
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4">
        <Video className="h-8 w-8 animate-pulse text-text-tertiary" aria-hidden />
        <div className="w-full max-w-[240px] text-center">
          {hasProgress ? (
            <div
              className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated"
              role="progressbar"
              aria-valuenow={progressPct ?? undefined}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full rounded-full bg-rose-500 transition-[width] duration-500"
                style={{
                  width: `${Math.min(100, Math.max(0, progressPct!))}%`,
                }}
              />
            </div>
          ) : null}
          <p className="shimmer-text text-sm font-medium">
            {hasProgress
              ? t("gen.videoPct", { pct: String(progressPct) })
              : t("gen.video")}
          </p>
          <p className="mt-0.5 text-xs text-text-muted">
            {etaText ?? t("gen.mayTakeAWhile")}
          </p>
        </div>
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            className="focus-ring rounded-full border border-border bg-bg-surface/80 px-3 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Cancel
          </button>
        ) : null}
      </div>
    </div>
  );
}
