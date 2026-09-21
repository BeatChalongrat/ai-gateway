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
  Download,
  Eye,
  Loader2,
  Play,
  XCircle,
} from "lucide-react";

import { StatusBadge } from "@/components/ui/FileTypeBadge";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";
import { previewKindFor, useFilePreviewOpener } from "./FilePreviewModal";

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
    <div className="my-3 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-sm">
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
        <span className="flex-shrink-0">{headerIcon}</span>
        <span className="text-sm font-semibold text-text-primary">{headerTitle}</span>
        {metadata ? (
          <span className="ml-auto text-xs text-text-muted">{metadata}</span>
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
      <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-text-muted">
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
        <span className="w-5 flex-shrink-0 text-right text-text-muted">{index}.</span>
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
  // Blue on purpose — a DECISION (FIX44 Part C), not a workaround.
  //
  // The app has two button families and this is the second one:
  //   1. neutral primary: bg-text-primary / bg-accent + text-bg-base
  //      (components/ui/Button "primary", UpdateBanner, ContextMeter) —
  //      inverts with the theme, reads as "the default action";
  //   2. action CTA: bg-blue-600 + text-white — used where the user is
  //      being asked to APPROVE something that will then run: this Run
  //      button, ConsentCard's allow buttons, PendingPreferenceCard,
  //      the Toast action. 8.6:1 on the button itself in both themes.
  //
  // History, so nobody re-learns it: --accent is a foreground hint colour
  // (#ECECEC dark / #0D0D0D light), so `bg-accent text-white` was
  // white-on-white in the dark theme. It was found here first and fixed
  // here only; the same pair lived on in UpdateBanner and ContextMeter
  // until FIX43.1. src/lib/contrast.test.ts now resolves every
  // bg-token + text-token pair through both themes and fails under AA, so
  // the next such pair fails in CI instead of in the owner's screenshot.
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
  // text-text-primary, border-border — so it tracks light/
  // dark automatically via the project's data-theme switch. No
  // dark: Tailwind classes (this project doesn't enable them).
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border bg-bg-elevated px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-surface disabled:cursor-not-allowed disabled:opacity-60"
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
  label,
}: {
  fileId: string;
  filename: string;
  label?: string;
}) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={() => void api.downloadFile(fileId, filename)}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border bg-bg-elevated px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-surface"
    >
      <Download className="h-4 w-4" />
      <span>{label ?? t("common.download")}</span>
    </button>
  );
}

// FIX110 — "Open" beside Download on a result card: the file the plan
// produced opens in the same viewer a user-attached file gets (the docked
// pane on the conversation page, the modal elsewhere). Rendered only for
// a type the viewer reads (xlsx/docx/pptx/pdf all are; previewKindFor
// decides) — a button that ends in "no preview" is worse than none.
// Verified before this existed: preview-data answered 200 with a sheet,
// a document and a deck for files made by the execute routes.
export function OpenPreviewButton({ file }: { file: FileAttachment }) {
  const t = useT();
  const { open, overlay } = useFilePreviewOpener();
  if (previewKindFor(file) === "unsupported") return null;
  return (
    <>
      <button
        type="button"
        data-testid="plan-result-open"
        onClick={() => open(file)}
        className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border bg-bg-elevated px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-surface"
      >
        <Eye className="h-4 w-4" />
        <span>{t("common.open")}</span>
      </button>
      {overlay}
    </>
  );
}

// MetadataRow renders the small footer-style row of badges (Version
// 2 · 12 KB · 5 slides ·…) under the result card's header.
export function MetadataRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
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
  okMessage,
}: ValidationBadgeProps) {
  const t = useT();
  if (!valid) {
    return (
      <div className="flex gap-2 rounded-md border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-status-danger">
        <XCircle className="h-4 w-4 flex-shrink-0" />
        <div className="min-w-0">
          <div className="font-medium">{t("plan.validation.failed")}</div>
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
          <div className="font-medium text-text-primary">{t("plan.validation.warnings")}</div>
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
    <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-bg-base px-3 py-2 text-xs text-text-secondary">
      <StatusBadge status="success" size="sm" />
      <span>{okMessage ?? t("plan.validation.ok")}</span>
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

// ── FIX126 F4 — a plan that already produced its file ─────────────────
//
// chat-edge links the file an execute produced to the assistant message
// that wrote the plan, so GET /messages returns it on that message's
// `files` with is_generated=true. The card then opens in its done state
// from that file instead of offering to generate the document again.

/** The generated file on this message that belongs to a plan of the given
 *  formats. A file whose name equals the plan's output_filename wins;
 *  otherwise the first generated file with a matching extension. */
export function linkedGeneratedFile(
  files: FileAttachment[] | undefined,
  extensions: readonly string[],
  outputFilename?: string,
): FileAttachment | undefined {
  if (!files || files.length === 0) return undefined;
  const wanted = new Set(extensions.map((e) => e.toLowerCase()));
  const matches = files.filter((f) => {
    if (f.is_generated !== true) return false;
    const i = f.filename.lastIndexOf(".");
    return i >= 0 && wanted.has(f.filename.slice(i + 1).toLowerCase());
  });
  if (matches.length === 0) return undefined;
  if (outputFilename) {
    const named = matches.find((f) => f.filename === outputFilename);
    if (named) return named;
  }
  return matches[0];
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The message id to send with an execute, or undefined while the row still
 *  carries a client-side temporary id ("asst-<uuid>"). */
export function serverMessageId(id: string | undefined): string | undefined {
  return id && UUID_RE.test(id) ? id : undefined;
}
