"use client";

// Compact "< 1/3 >" indicator + arrows for switching between
// alternative branches at a fork point. The parent owns the
// switch handler so the navigator stays pure UI.

import { useT } from "@/lib/i18n";

interface Props {
  branchIndex: number; // 0-based
  branchCount: number;
  onSwitch: (newIndex: number) => void;
  disabled?: boolean;
}

export function BranchNavigator({
  branchIndex,
  branchCount,
  onSwitch,
  disabled,
}: Props) {
  const t = useT();
  if (branchCount <= 1) return null;
  const canPrev = !disabled && branchIndex > 0;
  const canNext = !disabled && branchIndex < branchCount - 1;
  return (
    <div className="inline-flex items-center gap-1 rounded-full bg-bg-elevated/70 px-1.5 py-0.5 text-[11px] text-text-muted ring-1 ring-border">
      <button
        type="button"
        aria-label={t("a11y.prevBranch")}
        onClick={() => canPrev && onSwitch(branchIndex - 1)}
        disabled={!canPrev}
        className="focus-ring rounded p-0.5 transition-colors hover:bg-bg-secondary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
      >
        <svg width={10} height={10} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M15 6l-6 6 6 6"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <span className="tabular-nums">
        {branchIndex + 1}/{branchCount}
      </span>
      <button
        type="button"
        aria-label={t("a11y.nextBranch")}
        onClick={() => canNext && onSwitch(branchIndex + 1)}
        disabled={!canNext}
        className="focus-ring rounded p-0.5 transition-colors hover:bg-bg-secondary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
      >
        <svg width={10} height={10} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M9 6l6 6-6 6"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
