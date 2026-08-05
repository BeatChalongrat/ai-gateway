"use client";

// Sprint 3 — post-submit cost confirmation for premium media generations.
//
// Flow (decided in Sprint 3 planning): the form submits FIRST, then if the
// backend returns ``requires_confirmation === true`` (the $1.00 threshold is
// enforced server-side, so there is no client-side threshold constant) the
// job is reserved-but-not-polled and this modal is shown with the backend's
// exact figures. Cancel aborts via POST /v1/jobs/{id}/cancel (refund);
// Confirm starts polling. Built on the shared Modal (ESC + backdrop close,
// role=dialog, aria-modal, focus management).

import { useT } from "@/lib/i18n";

import { Modal } from "../ui/Modal";

interface Props {
  open: boolean;
  estimatedUsd: string;
  balanceBeforeUsd?: string;
  balanceAfterUsd?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function CostConfirmModal({
  open,
  estimatedUsd,
  balanceBeforeUsd,
  balanceAfterUsd,
  onConfirm,
  onCancel,
}: Props) {
  const t = useT();
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={t("media.confirm.title")}
      description={t("media.confirm.description")}
      maxWidth={420}
      footer={
        <>
          <button
            type="button"
            onClick={onCancel}
            className="focus-ring rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            autoFocus
            className="focus-ring rounded-full bg-rose-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-rose-600"
          >
            {t("media.confirm.confirm")}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-text-tertiary">{t("media.confirm.costApprox")}</span>
          <span className="text-base font-semibold text-text-primary">{estimatedUsd}</span>
        </div>
        {balanceAfterUsd ? (
          <div className="flex items-center justify-between">
            <span className="text-text-tertiary">{t("media.confirm.balanceAfter")}</span>
            <span className="text-text-secondary">
              {balanceBeforeUsd ? `${balanceBeforeUsd} → ` : ""}
              {balanceAfterUsd}
            </span>
          </div>
        ) : null}
        <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-500">
          {t("media.confirm.body")}
        </p>
      </div>
    </Modal>
  );
}
