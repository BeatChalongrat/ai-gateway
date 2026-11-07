"use client";

import Link from "next/link";
import { AnchorHTMLAttributes, ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "md" | "sm";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
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
    "bg-status-danger-solid hover:bg-status-danger-solid/90 text-white disabled:opacity-50",
};

// AM6 — "sm" is the small button the chat already writes by hand in its
// dialogs and cards (px-3 py-1.5 text-xs: NewProjectDialog, ShareModal,
// PendingPreferenceCard). "md" is the button as it always was.
const sizeClasses: Record<Size, string> = {
  md: "gap-2 rounded-md px-3.5 py-2 text-sm",
  sm: "gap-1.5 rounded-md px-3 py-1.5 text-xs",
};

const spinnerClasses: Record<Size, string> = {
  md: "h-4 w-4",
  sm: "h-3 w-3",
};

function buttonClasses(variant: Variant, size: Size, className: string): string {
  return `inline-flex items-center justify-center ${sizeClasses[size]} font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-border-strong ${variantClasses[variant]} ${className}`;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className = "", loading, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={buttonClasses(variant, size, className)}
      {...rest}
    >
      {loading ? (
        <span
          className={`${spinnerClasses[size]} animate-spin rounded-full border-2 border-white/30 border-t-white`}
          aria-hidden
        />
      ) : null}
      {children}
    </button>
  );
});

interface ButtonLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  href: string;
  variant?: Variant;
  size?: Size;
}

/** AM6 — a link that looks like the button: the Button's classes on a link,
 *  so it opens in a new tab and reads as a link to a screen reader. */
export const ButtonLink = forwardRef<HTMLAnchorElement, ButtonLinkProps>(function ButtonLink(
  { variant = "primary", size = "md", className = "", children, ...rest },
  ref,
) {
  return (
    <Link ref={ref} className={buttonClasses(variant, size, className)} {...rest}>
      {children}
    </Link>
  );
});
