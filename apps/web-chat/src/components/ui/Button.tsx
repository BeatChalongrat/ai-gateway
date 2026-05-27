"use client";

import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

// ChatGPT-style primary: inverted foreground/background — white pill
// + dark text in dark mode, near-black pill + light text in light
// mode. Drops the accent green that was reading as out-of-place
// across the redesigned UI (the only remaining accent in the app is
// the OpenAI provider mark on assistant avatars, where green = brand).
const variantClasses: Record<Variant, string> = {
  primary:
    "bg-text-primary hover:opacity-90 text-bg-base shadow-sm disabled:opacity-40 disabled:cursor-not-allowed",
  secondary:
    "bg-bg-secondary hover:bg-bg-elevated text-text-primary border border-border disabled:opacity-50",
  ghost:
    "bg-transparent hover:bg-bg-elevated text-text-secondary hover:text-text-primary",
  danger:
    "bg-status-danger/90 hover:bg-status-danger text-white disabled:opacity-50",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", className = "", loading, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-border-strong/40 ${variantClasses[variant]} ${className}`}
      {...rest}
    >
      {loading ? (
        <span
          className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white"
          aria-hidden
        />
      ) : null}
      {children}
    </button>
  );
});
