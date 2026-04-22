"use client";

import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  fullWidth?: boolean;
}

// Variant styles use CSS vars so they swap with the active theme.
// We attach inline style + a className for sizing/layout. Disabled
// state still uses Tailwind utilities (gray-300 etc.) intentionally —
// disabled buttons read OK against both light and dark surfaces.
const VARIANT_STYLE: Record<Variant, React.CSSProperties> = {
  primary: {
    background: "var(--accent-primary)",
    color: "#FFFFFF",
    border: "1px solid var(--accent-primary)",
  },
  secondary: {
    background: "var(--bg-surface)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-default)",
  },
  ghost: {
    background: "transparent",
    color: "var(--text-primary)",
    border: "1px solid transparent",
  },
  danger: {
    background: "var(--accent-danger)",
    color: "#FFFFFF",
    border: "1px solid var(--accent-danger)",
  },
};

const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-base",
};

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  {
    variant = "primary",
    size = "md",
    loading,
    fullWidth,
    className = "",
    style,
    disabled,
    children,
    ...rest
  },
  ref
) {
  const isDisabled = disabled || loading;
  // Primary + danger get the premium press feedback (indigo glow +
  // lift + settle). Secondary/ghost stay quiet — they're usually
  // Cancel-type actions that shouldn't be the louder affordance.
  const wantsPremium = variant === "primary" || variant === "danger";
  return (
    <button
      ref={ref}
      disabled={isDisabled}
      className={[
        "inline-flex items-center justify-center rounded-lg font-medium",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
        SIZE[size],
        fullWidth ? "w-full" : "",
        isDisabled ? "opacity-60 cursor-not-allowed" : "",
        wantsPremium && !isDisabled ? "btn-premium" : "",
        className,
      ].join(" ")}
      style={{ ...VARIANT_STYLE[variant], ...style }}
      {...rest}
    >
      {loading ? (
        <span className="inline-block h-4 w-4 border-2 border-white/60 border-t-transparent rounded-full animate-spin mr-2" />
      ) : null}
      {children}
    </button>
  );
});
