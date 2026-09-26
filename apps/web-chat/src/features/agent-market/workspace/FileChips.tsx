"use client";

import { Camera, X } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { formatBytes } from "@/lib/fileUtil";

import { useAmT } from "../i18n";
import { cls, Spin } from "../ui";
import type { Staged } from "./useUploads";

/** A sent file: name + size (user messages, late-file cards). */
export function FileChip({ name, size }: { name: string; size?: number }) {
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-lg border border-border bg-bg-base px-2.5 py-1.5 text-xs text-text-primary">
      <FileTypeBadge filename={name} size="sm" />
      <span className="min-w-0 truncate">{name}</span>
      {typeof size === "number" ? <span className="shrink-0 text-text-muted">{formatBytes(size)}</span> : null}
    </span>
  );
}

/** The agent's note on photos (manifest photo_notice), under the chips once
 *  a photo is attached. It informs; it never blocks the send. */
export function PhotoNotice({ text, className }: { text: string; className?: string }) {
  return (
    <p className={cls("flex items-start gap-1.5 text-xs leading-relaxed text-text-secondary", className)} data-testid="am-photo-notice">
      <Camera width={13} height={13} className="mt-0.5 shrink-0 text-status-warning" aria-hidden />
      <span className="min-w-0">{text}</span>
    </p>
  );
}

/** A file being staged: upload progress, error, remove. */
export function StagedChip({ item, onRemove }: { item: Staged; onRemove: () => void }) {
  const t = useAmT();
  return (
    <span
      className={cls(
        "inline-flex max-w-full items-center gap-2 rounded-lg border bg-bg-base py-1.5 pl-2.5 pr-1 text-xs",
        item.status === "error" ? "border-status-danger/50" : "border-border",
      )}
      data-testid="am-staged-chip"
      data-status={item.status}
    >
      <FileTypeBadge filename={item.name} size="sm" />
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-text-primary">{item.name}</span>
        <span className={cls("truncate", item.status === "error" ? "text-status-danger" : "text-text-muted")}>
          {item.status === "uploading"
            ? t("composer.uploading", { pct: String(item.progress) })
            : item.status === "error"
              ? item.error
              : formatBytes(item.size)}
        </span>
      </span>
      {item.status === "uploading" ? <Spin size={13} /> : null}
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("composer.removeFile", { name: item.name })}
        className="focus-ring inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-bg-elevated hover:text-text-primary"
      >
        <X width={13} height={13} aria-hidden />
      </button>
    </span>
  );
}
