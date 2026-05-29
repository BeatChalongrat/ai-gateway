"use client";

// Renders the file badges + inline image thumbnails that hang under
// a user message. Images get an inline preview that opens in the
// global lightbox on click; everything else gets a download pill.
//
// Both images and download pills go through the authenticated file
// endpoint (api.fileObjectUrl / api.downloadFile) rather than a bare
// <img src>/<a download>, because that route requires the Portal JWT
// in the Authorization header — see AuthedImage for the full why.

import { useRef, useState } from "react";

import { api } from "@/lib/api";
import { formatBytes, isImageMime } from "@/lib/fileUtil";
import type { FileAttachment } from "@/lib/types";
import { openLightbox } from "@/components/ui/ImageLightbox";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { AuthedImage } from "./AuthedImage";

interface Props {
  files: FileAttachment[];
}

export function MessageAttachments({ files }: Props) {
  if (!files || files.length === 0) return null;
  const images = files.filter((f) => isImageMime(f.content_type));
  const others = files.filter((f) => !isImageMime(f.content_type));
  return (
    <div className="mt-1 flex flex-col gap-1.5">
      {images.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {images.map((f) => (
            <ImageThumb key={f.id} file={f} />
          ))}
        </div>
      ) : null}
      {others.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {others.map((f) => (
            <FileBadge key={f.id} file={f} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ImageThumb({ file }: { file: FileAttachment }) {
  // AuthedImage hands us the loaded object URL; we reuse it for the
  // full-size lightbox so opening it doesn't trigger a second fetch.
  // The thumb stays mounted while the lightbox overlay is open, so the
  // URL is still valid (AuthedImage revokes only on unmount).
  const objectUrl = useRef<string | null>(null);
  return (
    <button
      type="button"
      onClick={() => {
        if (objectUrl.current) openLightbox(objectUrl.current, file.filename);
      }}
      className="focus-ring overflow-hidden rounded-md border border-border bg-bg-base"
      title={`${file.filename} · ${formatBytes(file.file_size_bytes)}`}
    >
      <AuthedImage
        fileId={file.id}
        alt={file.filename}
        onReady={(u) => (objectUrl.current = u)}
        className="block max-h-[200px] max-w-[300px] object-cover"
      />
    </button>
  );
}

function FileBadge({ file }: { file: FileAttachment }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await api.downloadFile(file.id, file.filename);
        } finally {
          setBusy(false);
        }
      }}
      className="focus-ring inline-flex max-w-[260px] items-center gap-2 rounded-md border border-border bg-bg-secondary px-2 py-1.5 text-[11px] text-text-secondary transition-colors hover:border-border-strong hover:bg-bg-elevated hover:text-text-primary disabled:opacity-60"
      title={`Download ${file.filename}`}
    >
      <FileTypeBadge
        filename={file.filename}
        contentType={file.content_type}
        size="sm"
      />
      <span className="min-w-0 flex-1 truncate text-left font-medium text-text-primary">
        {file.filename}
      </span>
      <span className="shrink-0 text-text-muted">
        {formatBytes(file.file_size_bytes)}
      </span>
    </button>
  );
}
