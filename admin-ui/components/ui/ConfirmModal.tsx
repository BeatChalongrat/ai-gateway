"use client";

import { useT } from "../../lib/i18n";
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
  // Defaults are resolved in the body, not in the parameter list, so they
  // re-render in the active locale instead of being frozen in English.
  title,
  message,
  confirmLabel,
  loading,
}: Props) {
  const t = useT();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title ?? t("confirm.title")}
      footer={
        <>
          <button className="sran-btn sran-btn-secondary" onClick={onClose} disabled={loading}>
            {t("action.cancel")}
          </button>
          <button className="sran-btn sran-btn-danger" onClick={onConfirm} disabled={loading}>
            {loading ? t("ui.modal.processing") : (confirmLabel ?? t("action.confirm"))}
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
