"use client";

// Pure presentational cost summary for the media-gen forms (Sprint 2A.5).
// Parent computes the values (from the submit response's USD siblings) and
// passes them in — this component makes no API calls.

import { useT } from "@/lib/i18n";

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
  const t = useT();
  return (
    <div className="rounded-lg border border-border bg-bg-secondary px-3 py-2 text-sm">
      <div className="flex items-center justify-between">
        <span className="text-text-muted">{t("media.cost.estimated")}</span>
        <span className="font-medium text-text-primary">{estimateUsd}</span>
      </div>
      {balanceBefore && balanceAfter ? (
        <div className="mt-1 flex items-center justify-between">
          <span className="text-text-muted">{t("media.cost.balance")}</span>
          <span className="text-text-secondary">
            {balanceBefore} → {balanceAfter}
          </span>
        </div>
      ) : null}
      {requiresConfirmation ? (
        <div className="mt-1.5 text-xs font-medium text-amber-400">
          {t("media.cost.premiumWarning")}
        </div>
      ) : null}
    </div>
  );
}
