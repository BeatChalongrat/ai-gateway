"use client";

// Shared shell + primitives for Excel / Doc / Pptx plan and result
// cards (Phase 4 Sessions 1-3, polished in Session 4). Pulling the
// pieces here keeps the three plan-card files focused on their
// per-format summarizers + icon maps instead of re-implementing
// the same chrome four times.
//
// Visual contract:
//   <PlanCard headerIcon headerTitle metadata?>
//     <body slot>     — list of typed icon + label rows
//     <footer slot>   — primary + secondary buttons
//   </PlanCard>
//
// All shells dark-mode friendly via the existing CSS variables
// the rest of the chat surface uses (bg-bg-surface / text-text-*).

import type { ReactNode } from "react";

import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Loader2,
  Play,
  XCircle,
} from "lucide-react";

import { api } from "@/lib/api";

interface CardShellProps {
  headerIcon: ReactNode;
  headerTitle: string;
  metadata?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

// CardShell is the visual frame both PlanCard and ResultCard wear.
// header + body + footer slots; consistent border, padding,
// dividers, and dark-mode treatment.
export function CardShell({
  headerIcon,
  headerTitle,
  metadata,
  children,
  footer,
}: CardShellProps) {
  // Outer border bumped from border-subtle (8%) to border-default
  // (12%) so the card frame stays visible on the darker chat
  // canvas; the inner header/footer dividers stay subtle for
  // visual hierarchy.
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border-default bg-bg-surface shadow-sm">
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
        <span className="flex-shrink-0">{headerIcon}</span>
        <span className="text-sm font-semibold text-text-primary">{headerTitle}</span>
        {metadata ? (
          <span className="ml-auto text-xs text-text-tertiary">{metadata}</span>
        ) : null}
      </div>
      <div className="space-y-3 px-4 py-3">{children}</div>
      {footer ? (
        <div className="flex items-center gap-2 border-t border-border-subtle bg-bg-base/40 px-4 py-3">
          {footer}
        </div>
      ) : null}
    </div>
  );
}

interface PlanListProps {
  title: string;
  // items are pre-rendered so each card can choose how to render
  // (Excel uses bullets, Pptx uses 1./2./3. numbered, Doc uses a
  // hybrid).
  children: ReactNode;
}

export function PlanList({ title, children }: PlanListProps) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-tertiary">
        {title}
      </div>
      <ul className="space-y-1 text-sm text-text-primary">{children}</ul>
    </div>
  );
}

interface PlanListItemProps {
  icon: ReactNode;
  index?: number;
  children: ReactNode;
}

export function PlanListItem({ icon, index, children }: PlanListItemProps) {
  return (
    <li className="flex items-start gap-2">
      <span className="mt-0.5 flex-shrink-0">{icon}</span>
      {index != null ? (
        <span className="w-5 flex-shrink-0 text-right text-text-tertiary">{index}.</span>
      ) : null}
      <span className="min-w-0 flex-1">{children}</span>
    </li>
  );
}

// PrimaryButton is the blue "Generate / Execute" CTA. SecondaryButton
// is the outline variant used for Download / Regenerate / Cancel.
interface ButtonProps {
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  type?: "button" | "submit";
}

export function PrimaryButton({
  onClick,
  disabled,
  loading,
  icon,
  children,
  type = "button",
}: ButtonProps) {
  // Hardcoded blue (not bg-accent) because the project's --accent
  // is a foreground hint colour — near-white in dark theme, near-
  // black in light — so a bg-accent + text-white button rendered
  // white-on-white on dark and disappeared. blue-600 is a solid
  // contrast colour against BOTH bg-base (white) and bg-base-dark
  // so the CTA pops regardless of theme.
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      className="focus-ring inline-flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-blue-700 hover:shadow-md active:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        icon ?? <Play className="h-4 w-4" />
      )}
      <span>{children}</span>
    </button>
  );
}

export function SecondaryButton({ onClick, disabled, icon, children }: ButtonProps) {
  // Uses the existing CSS-variable theme tokens — bg-bg-elevated,
  // text-text-primary, border-border-default — so it tracks light/
  // dark automatically via the project's data-theme switch. No
  // dark: Tailwind classes (this project doesn't enable them).
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border-default bg-bg-elevated px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-surface disabled:cursor-not-allowed disabled:opacity-60"
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

// DownloadButton — same surface as SecondaryButton but always a
// hyperlink so the browser's native download flow fires.
export function DownloadButton({
  fileId,
  filename,
  label = "Download",
}: {
  fileId: string;
  filename: string;
  label?: string;
}) {
  return (
    <a
      href={api.downloadUrl(fileId)}
      download={filename}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border-default bg-bg-elevated px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-surface"
    >
      <Download className="h-4 w-4" />
      <span>{label}</span>
    </a>
  );
}

// MetadataRow renders the small footer-style row of badges (Version
// 2 · 12 KB · 5 slides ·…) under the result card's header.
export function MetadataRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-tertiary">
      {children}
    </div>
  );
}

// ValidationBadge — green check / amber warning / red error
// banner. Three states; signal-only colour treatment so the user
// doesn't have to read the badge body to know the verdict.
interface ValidationBadgeProps {
  valid: boolean;
  warnings?: string[];
  errors?: string[];
  okMessage?: string;
}

export function ValidationBadge({
  valid,
  warnings = [],
  errors = [],
  okMessage = "File is valid.",
}: ValidationBadgeProps) {
  if (!valid) {
    return (
      <div className="flex gap-2 rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
        <XCircle className="h-4 w-4 flex-shrink-0" />
        <div className="min-w-0">
          <div className="font-medium">Validation failed</div>
          {errors.length > 0 ? (
            <ul className="mt-1 space-y-0.5 pl-4 list-disc">
              {errors.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    );
  }
  if (warnings.length > 0) {
    return (
      <div className="flex gap-2 rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-text-secondary">
        <AlertTriangle className="h-4 w-4 flex-shrink-0 text-status-warning" />
        <div className="min-w-0">
          <div className="font-medium text-text-primary">Validation passed with warnings</div>
          <ul className="mt-1 space-y-0.5 pl-4 list-disc">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-status-success">
      <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
      <span>{okMessage}</span>
    </div>
  );
}

export function ErrorLine({ message }: { message: string }) {
  return (
    <span
      role="alert"
      className="inline-flex items-center gap-1 text-xs text-status-danger"
    >
      <XCircle className="h-3.5 w-3.5" />
      {message}
    </span>
  );
}

// humanSize — KB/MB/GB formatter used by all three result cards.
export function humanSize(bytes: number): string {
  if (!bytes) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
