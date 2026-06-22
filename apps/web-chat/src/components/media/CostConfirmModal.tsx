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
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="⚠ Premium generation"
      description="This generation is above the confirmation threshold."
      maxWidth={420}
      footer={
        <>
          <button
            type="button"
            onClick={onCancel}
            className="focus-ring rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            autoFocus
            className="focus-ring rounded-full bg-rose-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-rose-600"
          >
            Confirm &amp; generate
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-text-tertiary">This will cost approximately</span>
          <span className="text-base font-semibold text-text-primary">{estimatedUsd}</span>
        </div>
        {balanceAfterUsd ? (
          <div className="flex items-center justify-between">
            <span className="text-text-tertiary">Balance after</span>
            <span className="text-text-secondary">
              {balanceBeforeUsd ? `${balanceBeforeUsd} → ` : ""}
              {balanceAfterUsd}
            </span>
          </div>
        ) : null}
        <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-500">
          Generation is reserved but not yet started. Cancel to refund, or
          confirm to begin processing. Once processing starts, the charge is
          final — even if cancelled mid-generation.
        </p>
      </div>
    </Modal>
  );
}
