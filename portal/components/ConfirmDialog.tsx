"use client";

import { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { Modal } from "./Modal";
import { Button } from "./Button";

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  loading?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "danger",
  loading,
}: Props) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            loading={loading}
            style={
              tone === "danger"
                ? {
                    background: "var(--accent-danger)",
                    borderColor: "var(--accent-danger)",
                  }
                : undefined
            }
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background:
              tone === "danger"
                ? "rgba(248,113,113,0.12)"
                : "rgba(99,102,241,0.12)",
            color:
              tone === "danger" ? "var(--accent-danger)" : "var(--accent-primary)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <AlertTriangle size={18} />
        </div>
        <div style={{ flex: 1, fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.55 }}>
          {message}
        </div>
      </div>
    </Modal>
  );
}
