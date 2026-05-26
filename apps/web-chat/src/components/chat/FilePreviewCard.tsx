"use client";

// Card rendered between the textarea and the chat thread while a
// file is uploading or waiting to be sent. Shows thumbnail for
// images, file-icon emoji + size for everything else, plus a
// progress bar and a remove button.

import { useEffect, useState } from "react";

import type { PendingFile } from "@/stores/chatStore";
import {
  formatBytes,
  iconEmojiFor,
  iconKindFor,
  isImageMime,
} from "@/lib/fileUtil";

interface Props {
  pending: PendingFile;
  onRemove: () => void;
  onRetry?: () => void;
}

export function FilePreviewCard({ pending, onRemove, onRetry }: Props) {
  const isImage = isImageMime(pending.file.type);
  const [thumb, setThumb] = useState<string | null>(null);

  // For image uploads, generate a local object URL once so the card
  // can render a 48px preview. Revoke on unmount to avoid leaks.
  useEffect(() => {
    if (!isImage) return;
    const url = URL.createObjectURL(pending.file);
    setThumb(url);
    return () => URL.revokeObjectURL(url);
  }, [isImage, pending.file]);

  const kind = iconKindFor(pending.file.name, pending.file.type);
  const sizeStr = formatBytes(pending.file.size);
  const isUploading = pending.status === "uploading";
  const isReady = pending.status === "ready";
  const isError = pending.status === "error";

  return (
    <div
      className={`group relative flex shrink-0 items-start gap-2 rounded-lg border px-2.5 py-2 text-xs transition-colors ${
        isError
          ? "border-status-danger/40 bg-status-danger/10"
          : "border-border bg-bg-secondary"
      }`}
      style={{ minWidth: 200, maxWidth: 260 }}
    >
      {isImage && thumb ? (
        <img
          src={thumb}
          alt={pending.file.name}
          className="h-12 w-12 shrink-0 rounded object-cover ring-1 ring-border"
        />
      ) : (
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded bg-bg-base text-2xl"
          aria-hidden
        >
          {iconEmojiFor(kind)}
        </div>
      )}

      <div className="min-w-0 flex-1 pr-5">
        <div
          className="truncate font-medium text-text-primary"
          title={pending.file.name}
        >
          {pending.file.name}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-[10px] text-text-muted">
          <span>{sizeStr}</span>
          <span>·</span>
          <span className="uppercase tracking-wider">{kind}</span>
          {isReady ? (
            <span className="ml-auto inline-flex items-center text-status-success" aria-label="Ready">
              <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M5 13l4 4L19 7"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
          ) : null}
        </div>
        {isUploading ? (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-bg-elevated">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${pending.progress}%` }}
            />
          </div>
        ) : null}
        {isError ? (
          <div className="mt-1 flex items-center gap-1 text-[10px] text-status-danger">
            <span className="truncate" title={pending.error}>
              {pending.error ?? "Upload failed"}
            </span>
            {onRetry ? (
              <button
                type="button"
                onClick={onRetry}
                className="focus-ring rounded px-1 py-0.5 underline hover:text-text-primary"
              >
                Retry
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <button
        type="button"
        aria-label="Remove file"
        onClick={onRemove}
        className="focus-ring absolute right-1 top-1 rounded p-0.5 text-text-muted opacity-70 transition-opacity hover:bg-bg-elevated hover:text-text-primary group-hover:opacity-100"
      >
        <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M6 6l12 12M18 6l-12 12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </button>
    </div>
  );
}
