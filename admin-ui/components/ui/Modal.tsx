"use client";

import { useEffect } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

export default function Modal({ open, onClose, title, children, footer }: Props) {
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
      return () => { document.body.style.overflow = ""; };
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 50,
        background: "rgba(15, 31, 56, 0.5)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "1rem",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "white", borderRadius: "var(--radius-xl)",
          boxShadow: "0 16px 48px -8px rgba(15, 31, 56, 0.18), 0 4px 12px rgba(15, 31, 56, 0.08)",
          border: "1px solid var(--color-border-default)",
          width: "100%", maxWidth: "28rem",
          maxHeight: "85vh", overflow: "auto",
        }}
      >
        <div style={{
          padding: "1.25rem 1.5rem", borderBottom: "1px solid var(--color-border-default)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <h2 style={{ fontSize: "var(--font-size-lg)", fontWeight: 600, margin: 0 }}>{title}</h2>
          <button
            onClick={onClose}
            style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: "1.25rem", color: "var(--color-text-muted)", padding: "0.25rem",
            }}
          >
            ✕
          </button>
        </div>
        <div style={{ padding: "1.5rem" }}>{children}</div>
        {footer && (
          <div style={{
            padding: "1rem 1.5rem", borderTop: "1px solid var(--color-border-default)",
            display: "flex", justifyContent: "flex-end", gap: "0.75rem",
          }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
