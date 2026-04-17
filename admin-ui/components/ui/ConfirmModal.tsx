"use client";

import Modal from "./Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title?: string;
  message: string;
  confirmLabel?: string;
  loading?: boolean;
}

export default function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title = "Confirm Action",
  message,
  confirmLabel = "Confirm",
  loading,
}: Props) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="sran-btn sran-btn-danger" onClick={onConfirm} disabled={loading}>
            {loading ? "Processing..." : confirmLabel}
          </button>
        </>
      }
    >
      <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-secondary)", margin: 0, lineHeight: 1.6 }}>
        {message}
      </p>
    </Modal>
  );
}
