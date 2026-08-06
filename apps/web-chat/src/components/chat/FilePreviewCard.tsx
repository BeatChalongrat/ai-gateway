"use client";

// Card rendered between the textarea and the chat thread for each
// file currently staged on the next outgoing message.
//
// Two layouts share the same outer container size so the flex-wrap
// row stays visually regular regardless of mix:
//
//   ImagePreviewCard    — 160×140 with a 100px-tall thumbnail
//                         (blob URL via createObjectURL), name +
//                         size beneath, optional uploading/error
//                         overlay.
//   DocumentPreviewCard — 160×140 with the monochrome FileTypeBadge
//                         centred, name + size beneath.
//
// Both render a hover-only ✕ button (backdrop-blurred chip) and a
// status-aware overlay (spinner while uploading, alert+retry on
// error). FilePreviewCard dispatches to the right layout based on
// MIME / extension.

import { useEffect, useState } from "react";

import { AlertTriangle, ImageIcon, Loader2, RotateCw, X } from "lucide-react";

import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { useT } from "@/lib/i18n";
import { isExtracting } from "@/lib/fileExtraction";
import type { PendingFile } from "@/stores/chatStore";
import {
  fileExtension,
  formatBytes,
  isImageMime,
} from "@/lib/fileUtil";

interface Props {
  pending: PendingFile;
  onRemove: () => void;
  onRetry?: () => void;
}

// Common card shell — gets the size, rounded corners, status
// overlay, and remove/retry chrome. Children render whatever sits
// above the metadata line (thumbnail for images, big badge for
// documents).
function CardShell({
  children,
  name,
  size,
  status,
  extracting,
  progress,
  errorMsg,
  onRemove,
  onRetry,
}: {
  children: React.ReactNode;
  name: string;
  size: number;
  status: PendingFile["status"];
  /** Bytes are stored but the server is still reading the document
   *  (OCR on a scan takes minutes). Distinct from `uploading`: this
   *  state does NOT block sending, so it gets a quiet inline label
   *  rather than the blocking dim-and-spin overlay. */
  extracting?: boolean;
  /** 0..100 from the XHR upload-progress event. Phase 7: this was
   *  already being measured and stored per pending file but never
   *  surfaced — the card showed a bare spinner, so a large upload
   *  looked identical to a stuck one. */
  progress?: number;
  errorMsg?: string;
  onRemove: () => void;
  onRetry?: () => void;
}) {
  const t = useT();
  const isError = status === "error";
  const isUploading = status === "uploading";
  const pct = Math.max(0, Math.min(100, Math.round(progress ?? 0)));
  const isExtracting = Boolean(extracting) && !isUploading && !isError;
  return (
    <div
      className={`group relative flex flex-col overflow-hidden rounded-xl border transition-colors ${
        isError
          ? "border-status-danger/40 bg-status-danger/5"
          : "border-border-default bg-bg-secondary"
      }`}
      style={{ width: 160 }}
    >
      {children}

      <div className="px-2.5 py-2">
        <p
          className="truncate text-xs font-medium text-text-primary"
          title={name}
        >
          {name}
        </p>
        {isExtracting ? (
          <p className="mt-0.5 flex items-center gap-1 text-[11px] text-text-tertiary">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            <span>{t("file.reading")}</span>
          </p>
        ) : (
          <p className="mt-0.5 text-[11px] text-text-tertiary">
            {formatBytes(size)}
          </p>
        )}
        {isError && errorMsg ? (
          <p
            className="mt-1 truncate text-[10px] text-status-danger"
            title={errorMsg}
          >
            {errorMsg}
          </p>
        ) : null}
      </div>

      {isUploading ? (
        <div
          className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/35 backdrop-blur-[1px]"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={t("file.uploading")}
          data-testid="upload-progress"
        >
          <Loader2 className="h-5 w-5 animate-spin text-white" aria-hidden />
          <span className="text-xs font-medium tabular-nums text-white">
            {pct}%
          </span>
          <div className="h-1 w-20 overflow-hidden rounded-full bg-white/30">
            <div
              className="h-full rounded-full bg-white transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      ) : null}

      {isError ? (
        <div className="pointer-events-none absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-status-danger/85 text-white">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        </div>
      ) : null}

      {/* Hover-only remove ✕. Appears on group-hover OR keyboard
          focus-within so keyboard users can find it without a
          pointer. backdrop-blur keeps the contrast legible over a
          dark image thumbnail. */}
      <button
        type="button"
        aria-label={t("file.remove")}
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        className="focus-ring absolute left-1.5 top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full border border-border-default bg-bg-surface/85 text-text-secondary opacity-0 backdrop-blur-sm transition-opacity hover:bg-bg-surface hover:text-text-primary group-hover:opacity-100 group-focus-within:opacity-100"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>

      {isError && onRetry ? (
        <button
          type="button"
          aria-label={t("file.retryUpload")}
          onClick={(e) => {
            e.stopPropagation();
            onRetry();
          }}
          className="focus-ring absolute bottom-2 right-2 inline-flex h-6 items-center gap-1 rounded-full border border-border-default bg-bg-surface/85 px-2 text-[10px] font-medium text-text-secondary backdrop-blur-sm transition-colors hover:bg-bg-surface hover:text-text-primary"
        >
          <RotateCw className="h-3 w-3" aria-hidden />
          Retry
        </button>
      ) : null}
    </div>
  );
}

function ImagePreviewCard({ pending, onRemove, onRetry }: Props) {
  // Generate a local blob URL once for the thumbnail. createObjectURL
  // is synchronous and cheap; revoke on unmount so we don't leak
  // memory if the user attaches a lot of images in a session.
  const [thumb, setThumb] = useState<string | null>(null);
  useEffect(() => {
    const url = URL.createObjectURL(pending.file);
    setThumb(url);
    return () => URL.revokeObjectURL(url);
  }, [pending.file]);

  return (
    <CardShell
      name={pending.file.name}
      size={pending.file.size}
      status={pending.status}
      progress={pending.progress}
      extracting={isExtracting(pending.extractionStatus)}
      errorMsg={pending.error}
      onRemove={onRemove}
      onRetry={onRetry}
    >
      <div
        className="relative bg-bg-elevated"
        style={{ height: 100, width: "100%" }}
      >
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumb}
            alt={pending.file.name}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-text-tertiary">
            <ImageIcon className="h-7 w-7" aria-hidden />
          </div>
        )}
      </div>
    </CardShell>
  );
}

function DocumentPreviewCard({ pending, onRemove, onRetry }: Props) {
  const ext = fileExtension(pending.file.name);
  return (
    <CardShell
      name={pending.file.name}
      size={pending.file.size}
      status={pending.status}
      progress={pending.progress}
      extracting={isExtracting(pending.extractionStatus)}
      errorMsg={pending.error}
      onRemove={onRemove}
      onRetry={onRetry}
    >
      <div
        className="flex items-center justify-center bg-bg-elevated"
        style={{ height: 100, width: "100%" }}
      >
        <FileTypeBadge
          extension={ext ? `.${ext}` : undefined}
          filename={pending.file.name}
          contentType={pending.file.type}
          size="lg"
        />
      </div>
    </CardShell>
  );
}

export function FilePreviewCard({ pending, onRemove, onRetry }: Props) {
  const isImage =
    isImageMime(pending.file.type) ||
    /\.(png|jpe?g|gif|webp|svg)$/i.test(pending.file.name);
  return isImage ? (
    <ImagePreviewCard pending={pending} onRemove={onRemove} onRetry={onRetry} />
  ) : (
    <DocumentPreviewCard
      pending={pending}
      onRemove={onRemove}
      onRetry={onRetry}
    />
  );
}
