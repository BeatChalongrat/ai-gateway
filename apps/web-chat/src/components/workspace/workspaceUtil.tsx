// Shared utilities + icon mapping for the Phase-4 Session-5
// Artifact Workspace.

import type { ReactNode } from "react";
import {
  ArrowUpDown,
  FileText,
  Hash,
  Minus,
  Plus,
  Presentation,
  Sheet,
} from "lucide-react";

// File-type icon picker — covers everything the generators emit
// (xlsx, docx, pdf, pptx) plus a generic fallback. Colour-coded
// by format for a quick visual scan in the panel list.
export function fileTypeIcon(filename: string, contentType?: string): ReactNode {
  const ct = (contentType || "").toLowerCase();
  const lower = filename.toLowerCase();
  if (ct.includes("spreadsheet") || lower.endsWith(".xlsx") || lower.endsWith(".xls")) {
    return <Sheet className="h-5 w-5 text-emerald-500" />;
  }
  if (ct.includes("wordprocessing") || lower.endsWith(".docx")) {
    return <FileText className="h-5 w-5 text-blue-500" />;
  }
  if (ct.includes("pdf") || lower.endsWith(".pdf")) {
    return <FileText className="h-5 w-5 text-rose-500" />;
  }
  if (ct.includes("presentation") || lower.endsWith(".pptx")) {
    return <Presentation className="h-5 w-5 text-amber-500" />;
  }
  return <FileText className="h-5 w-5 text-text-tertiary" />;
}

// Compact byte formatter — keeps file cards readable at panel
// widths of ~320px. Returns "0 B" / "12.5 KB" / "1.2 MB" etc.
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

// "2 min ago" — short relative-time formatter for timeline cards.
// Drops to absolute date when older than 7 days.
export function timeAgo(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const seconds = Math.max(0, (Date.now() - t) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} day ago`;
  return new Date(t).toLocaleDateString();
}

// changeIcon — per-change-type icon for the comparison card.
// Same colour convention as the plan cards (emerald=added,
// rose=removed, blue=structural, slate=neutral).
export function changeIcon(type: string): ReactNode {
  if (type.endsWith("_added")) {
    return <Plus className="h-3.5 w-3.5 text-emerald-500" />;
  }
  if (type.endsWith("_removed")) {
    return <Minus className="h-3.5 w-3.5 text-rose-500" />;
  }
  if (type === "word_count_changed") {
    return <Hash className="h-3.5 w-3.5 text-text-tertiary" />;
  }
  return <ArrowUpDown className="h-3.5 w-3.5 text-blue-500" />;
}
