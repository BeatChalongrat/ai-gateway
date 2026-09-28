"use client";

// A file that belongs to a message, once: the type badge, the name, the
// size, what opening it does, and a download. The chat shows its sent
// attachments with it (MessageAttachments, which knows how to preview and
// fetch a chat file); an agent's workspace shows the files a person sent to
// the agent with it (AM5). What a click does is the caller's.
//
// FIX110 — the name has opened the preview since Phase 10, but nothing
// said so: no label, no icon, and the hover tint was shared with the
// download button, so "download only" was a fair reading of the card.
// The size line now ends with "· Open" (an eye) for a file the viewer can
// show; for one it cannot the name simply downloads — no affordance that
// leads to "no preview for this type". Same height either way: the second
// line already existed.

import type { ReactNode } from "react";
import { Download, Eye } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { formatBytes } from "@/lib/fileUtil";
import { useT } from "@/lib/i18n";

export function AttachmentCard({
  filename,
  contentType,
  sizeBytes,
  onOpen,
  onDownload,
  busy = false,
  downloadLabel,
  note,
}: {
  filename: string;
  contentType?: string;
  sizeBytes?: number;
  /** Present: the name opens the file (and the card says so). Absent: the name downloads. */
  onOpen?: () => void;
  /** Absent: a file that can only be named (nothing to fetch it with). */
  onDownload?: () => void;
  busy?: boolean;
  downloadLabel: string;
  /** Under the size line (a file the server could not read says so, and why). */
  note?: ReactNode;
}) {
  const t = useT();
  const previewable = !!onOpen;
  const onName = onOpen ?? onDownload;
  return (
    <div className="group flex w-full max-w-[300px] items-center gap-1 rounded-lg border border-border bg-bg-secondary pr-1 transition-colors hover:border-border-strong hover:bg-bg-elevated">
      <button
        type="button"
        onClick={onName}
        disabled={!onName || (!previewable && busy)}
        data-testid={previewable ? "file-card-preview" : "file-card-name-download"}
        className="focus-ring flex min-w-0 flex-1 items-center gap-3 p-3 text-left"
        title={previewable ? `${t("common.open")} · ${filename}` : filename}
      >
        <FileTypeBadge
          filename={filename}
          contentType={contentType}
          size="md"
          className="shrink-0"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-text-primary">
            {filename}
          </span>
          <span className="block text-xs text-text-muted">
            {typeof sizeBytes === "number" ? formatBytes(sizeBytes) : null}
            {previewable ? (
              <span
                data-testid="file-card-open-hint"
                className="ml-1.5 inline-flex items-center gap-0.5 text-text-secondary group-hover:text-text-primary"
              >
                · <Eye className="h-3 w-3" aria-hidden />
                {t("common.open")}
              </span>
            ) : null}
          </span>
          {note}
        </span>
      </button>
      {onDownload ? (
        <button
          type="button"
          disabled={busy}
          aria-label={downloadLabel}
          data-testid="file-card-download"
          onClick={onDownload}
          className="focus-ring shrink-0 rounded-md p-2 text-text-muted transition-colors hover:bg-bg-surface hover:text-text-primary disabled:opacity-60"
        >
          <Download className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
