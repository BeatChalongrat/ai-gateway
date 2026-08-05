// Shared utilities for the Phase-4 Session-5 Artifact Workspace.
//
// Session 5 originally shipped colour-coded Lucide icons here
// (emerald sheet, blue doc, rose pdf, amber pptx, +emerald/-rose
// change icons). Session 5.1 retints everything to the
// monochrome FileTypeBadge / muted-Lucide system so the workspace
// reads as a single visual language with the blue primary CTA as
// the only accent.

import type { ReactNode } from "react";
import { ArrowUpDown, Minus, Plus } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { tOutside } from "@/lib/i18n";

// fileTypeIcon — the workspace + timeline call this for per-file
// glyphs. Returns a FileTypeBadge so every format gets the same
// monochrome document outline + type label treatment.
export function fileTypeIcon(filename: string, contentType?: string): ReactNode {
  return <FileTypeBadge filename={filename} contentType={contentType} size="md" />;
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
  if (seconds < 60) return tOutside("time.justNow");
  if (seconds < 3600)
    return tOutside("time.minAgo", { n: String(Math.floor(seconds / 60)) });
  if (seconds < 86400)
    return tOutside("time.hrAgo", { n: String(Math.floor(seconds / 3600)) });
  if (seconds < 604800)
    return tOutside("time.dayAgo", { n: String(Math.floor(seconds / 86400)) });
  return new Date(t).toLocaleDateString();
}

// changeIcon — per-change-type glyph for the comparison card.
// All in text-text-tertiary so the diff reads as structural
// metadata, not as a coloured alarm. The Plus / Minus / arrow
// shapes alone carry the "added / removed / changed" signal.
export function changeIcon(type: string): ReactNode {
  if (type.endsWith("_added")) {
    return <Plus className="h-3.5 w-3.5 text-text-tertiary" />;
  }
  if (type.endsWith("_removed")) {
    return <Minus className="h-3.5 w-3.5 text-text-tertiary" />;
  }
  return <ArrowUpDown className="h-3.5 w-3.5 text-text-tertiary" />;
}
