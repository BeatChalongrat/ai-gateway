"use client";

// Files in an agent's workspace are the chat's files (AM5): a file being
// staged is the chat's file card (FilePreviewCard — thumbnail or type badge,
// upload progress, the reason it failed, remove and try again), a file that
// was sent is the chat's attachment card (AttachmentCard). What is the
// agent's own is where a file is fetched from, and the note on photos.

import { useState } from "react";
import { Camera } from "lucide-react";

import { AttachmentCard } from "@/components/chat/AttachmentCard";
import { FilePreviewCard } from "@/components/chat/FilePreviewCard";

import { amApi } from "../api";
import { useAmT } from "../i18n";
import { cls, Thai } from "../ui";
import { toPending, type Staged } from "./useUploads";

/** A sent file (the person's message, a late-file card). With its id it downloads. */
export function FileChip({ id, name, size }: { id?: string; name: string; size?: number }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const download = id
    ? () => {
        setBusy(true);
        amApi.downloadFile(id, name).then(
          () => setBusy(false),
          () => setBusy(false),
        );
      }
    : undefined;
  return (
    <Thai>
      <AttachmentCard filename={name} sizeBytes={size} onDownload={download} busy={busy} downloadLabel={t("common.downloadNamed", { name })} />
    </Thai>
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

/** A file being staged: upload progress, error, remove, try again. */
export function StagedChip({ item, onRemove, onRetry }: { item: Staged; onRemove: () => void; onRetry?: () => void }) {
  return (
    <div className="contents" data-testid="am-staged-chip" data-status={item.status} data-name={item.name}>
      <Thai>
        <FilePreviewCard
          pending={toPending(item)}
          onRemove={onRemove}
          onRetry={item.status === "error" && item.retryable !== false ? onRetry : undefined}
        />
      </Thai>
    </div>
  );
}
