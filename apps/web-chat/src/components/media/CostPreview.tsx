"use client";

// Pure presentational cost summary for the media-gen forms (Sprint 2A.5).
// Parent computes the values (from the submit response's USD siblings) and
// passes them in — this component makes no API calls.

interface Props {
  estimateUsd: string;
  balanceBefore?: string;
  balanceAfter?: string;
  requiresConfirmation?: boolean;
}

export function CostPreview({
  estimateUsd,
  balanceBefore,
  balanceAfter,
  requiresConfirmation,
}: Props) {
  return (
    <div className="rounded-lg border border-border bg-bg-secondary px-3 py-2 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-text-tertiary">Estimated cost</span>
        <span className="font-medium text-text-primary">{estimateUsd}</span>
      </div>
      {balanceBefore && balanceAfter ? (
        <div className="mt-1 flex items-center justify-between">
          <span className="text-text-tertiary">Balance</span>
          <span className="text-text-secondary">
            {balanceBefore} → {balanceAfter}
          </span>
        </div>
      ) : null}
      {requiresConfirmation ? (
        <div className="mt-1.5 text-xs font-medium text-amber-400">
          ⚠ Premium action — confirmation required
        </div>
      ) : null}
    </div>
  );
}
