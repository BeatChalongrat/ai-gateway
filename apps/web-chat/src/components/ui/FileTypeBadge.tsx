"use client";

// Monochrome file-type + status badges.
//
// Replaces the colour-coded Lucide icons that crept in during
// Phase 4 Sessions 1-4 (Sheet emerald / FileText blue / Presentation
// amber / etc.) with a single muted-grey "document outline + type
// label" shape and a circle-outlined status indicator. Result: a
// cleaner visual hierarchy where the only colour in the workspace
// surface is the blue primary CTA.
//
// Theming: uses the project's text-text-tertiary token (resolves
// via the data-theme CSS variable swap), so the badge tracks
// light↔dark without `dark:` utility classes (which aren't enabled
// in this project's Tailwind config).

import type { ReactNode } from "react";

import { AlertTriangle, Check, Loader2, X } from "lucide-react";

// Tiny class combiner — the project doesn't ship a shared `cn`
// helper. Keeping it inline so this file has no new dependencies.
function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ─── FileTypeBadge ────────────────────────────────────────────
const EXTENSION_LABELS: Record<string, string> = {
  ".xlsx": "XLS",
  ".xls":  "XLS",
  ".csv":  "CSV",
  ".docx": "DOC",
  ".doc":  "DOC",
  ".pdf":  "PDF",
  ".pptx": "PPT",
  ".ppt":  "PPT",
  ".png":  "IMG",
  ".jpg":  "IMG",
  ".jpeg": "IMG",
  ".gif":  "IMG",
  ".webp": "IMG",
  ".svg":  "SVG",
  ".txt":  "TXT",
  ".md":   "MD",
  ".json": "JSON",
  ".py":   "PY",
  ".go":   "GO",
};

interface FileTypeBadgeProps {
  extension?: string; // ".xlsx", ".pdf", … case-insensitive
  filename?: string;  // when extension is unknown, parse from filename
  contentType?: string; // last-resort fallback when neither is set
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE_CONFIG: Record<"sm" | "md" | "lg", { wrapper: string; text: string; labelY: string }> = {
  sm: { wrapper: "w-6 h-7",  text: "text-[7px]", labelY: "bottom-0.5" },
  md: { wrapper: "w-8 h-10", text: "text-[8px]", labelY: "bottom-1" },
  lg: { wrapper: "w-10 h-12", text: "text-[9px]", labelY: "bottom-1.5" },
};

function inferExtension({ extension, filename, contentType }: FileTypeBadgeProps): string {
  if (extension) return extension;
  if (filename) {
    const dot = filename.lastIndexOf(".");
    if (dot >= 0 && dot < filename.length - 1) {
      return filename.slice(dot);
    }
  }
  if (contentType) {
    const ct = contentType.toLowerCase();
    if (ct.includes("spreadsheet")) return ".xlsx";
    if (ct.includes("wordprocessing")) return ".docx";
    if (ct.includes("pdf")) return ".pdf";
    if (ct.includes("presentation")) return ".pptx";
    if (ct.startsWith("image/")) return ".png";
    if (ct.startsWith("text/")) return ".txt";
  }
  return "";
}

export function FileTypeBadge({
  extension,
  filename,
  contentType,
  size = "md",
  className,
}: FileTypeBadgeProps): ReactNode {
  const ext = inferExtension({ extension, filename, contentType });
  const lower = ext.toLowerCase();
  const label = EXTENSION_LABELS[lower] ||
    (lower ? lower.replace(".", "").toUpperCase().slice(0, 4) : "FILE");
  const config = SIZE_CONFIG[size];
  return (
    <div
      className={cls(
        "relative flex-shrink-0 text-text-tertiary",
        config.wrapper,
        className,
      )}
      aria-hidden
    >
      <svg viewBox="0 0 32 40" fill="none" className="h-full w-full">
        {/* Document body */}
        <path
          d="M2 4C2 2.89543 2.89543 2 4 2H20L30 12V36C30 37.1046 29.1046 38 28 38H4C2.89543 38 2 37.1046 2 36V4Z"
          stroke="currentColor"
          strokeWidth="1.5"
        />
        {/* Folded corner */}
        <path
          d="M20 2V10C20 11.1046 20.8954 12 22 12H30"
          stroke="currentColor"
          strokeWidth="1.5"
        />
      </svg>
      <span
        className={cls(
          "absolute left-1/2 -translate-x-1/2 font-mono font-bold leading-none tracking-tight",
          config.text,
          config.labelY,
        )}
      >
        {label}
      </span>
    </div>
  );
}

// ─── StatusBadge ──────────────────────────────────────────────
interface StatusBadgeProps {
  status: "success" | "error" | "warning" | "loading";
  size?: "sm" | "md";
  className?: string;
}

export function StatusBadge({ status, size = "md", className }: StatusBadgeProps): ReactNode {
  const wrap = size === "sm" ? "h-5 w-5" : "h-6 w-6";
  const inner = size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5";

  // Monochrome by default. Only error + warning carry colour so
  // the user can spot real problems at a glance; "success" is
  // intentionally muted (most status badges are successes — no
  // need to scream green).
  let icon: ReactNode;
  let tint = "text-text-tertiary";
  switch (status) {
    case "error":
      icon = <X className={inner} />;
      tint = "text-status-danger border-status-danger/60";
      break;
    case "warning":
      icon = <AlertTriangle className={inner} />;
      tint = "text-status-warning border-status-warning/60";
      break;
    case "loading":
      icon = <Loader2 className={cls(inner, "animate-spin")} />;
      break;
    case "success":
    default:
      icon = <Check className={inner} />;
      break;
  }

  return (
    <span
      className={cls(
        "inline-flex flex-shrink-0 items-center justify-center rounded-full border border-text-tertiary/60",
        wrap,
        tint,
        className,
      )}
      aria-hidden
    >
      {icon}
    </span>
  );
}
