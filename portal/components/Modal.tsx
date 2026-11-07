"use client";

import { ReactNode, useEffect } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
  widthClass?: string;
}

export function Modal({ open, onClose, title, children, footer, widthClass = "max-w-md" }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={["w-full rounded-xl shadow-2xl", widthClass].join(" ")}
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
        }}
      >
        {title ? (
          <div
            className="px-6 py-4"
            style={{ borderBottom: "1px solid var(--border-subtle)" }}
          >
            <h2
              className="font-heading"
              style={{
                fontSize: 17,
                fontWeight: 600,
                color: "var(--text-primary)",
                margin: 0,
              }}
            >
              {title}
            </h2>
          </div>
        ) : null}
        <div className="px-6 py-5" style={{ color: "var(--text-primary)" }}>
          {children}
        </div>
        {footer ? (
          <div
            className="px-6 py-4 rounded-b-xl flex justify-end gap-2"
            style={{
              borderTop: "1px solid var(--border-subtle)",
              background: "var(--bg-surface-2)",
            }}
          >
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
