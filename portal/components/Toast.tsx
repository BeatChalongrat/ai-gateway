"use client";

import { createContext, useCallback, useContext, useState } from "react";
import { Check, Info, X } from "lucide-react";

type ToastType = "success" | "error" | "info";

interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastCtx {
  toast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastCtx>({ toast: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

let nextId = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((message: string, type: ToastType = "success") => {
    const id = ++nextId;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(
      () => setToasts((prev) => prev.filter((t) => t.id !== id)),
      3000
    );
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        aria-live="polite"
        style={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 100,
          display: "flex",
          flexDirection: "column",
          gap: 10,
          pointerEvents: "none",
          maxWidth: "calc(100vw - 48px)",
        }}
      >
        {toasts.map((t) => {
          const color =
            t.type === "success"
              ? "var(--accent-success)"
              : t.type === "error"
              ? "var(--accent-danger)"
              : "var(--accent-primary)";
          const icon =
            t.type === "success" ? (
              <Check size={14} strokeWidth={2.5} />
            ) : t.type === "error" ? (
              <X size={14} strokeWidth={2.5} />
            ) : (
              <Info size={14} strokeWidth={2.5} />
            );
          return (
            <div
              key={t.id}
              role="status"
              className="toast-in"
              style={{
                pointerEvents: "auto",
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 14px",
                borderRadius: 10,
                background: "var(--bg-surface)",
                border: `1px solid ${color}`,
                boxShadow:
                  "0 10px 28px -10px rgba(10,18,48,0.35), 0 2px 6px rgba(10,18,48,0.1)",
                color: "var(--text-primary)",
                fontSize: 13,
                fontWeight: 500,
                minWidth: 240,
              }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: `${color}22`,
                  color,
                  flexShrink: 0,
                }}
              >
                {icon}
              </span>
              <span style={{ flex: 1 }}>{t.message}</span>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
