"use client";

// Renders the file badges + inline image thumbnails that hang under
// a user message. Images get an inline preview that opens in the
// global lightbox on click; everything else gets a download pill.
//
// Both images and download pills go through the authed client
// (api.fileObjectUrl / api.downloadFile) rather than a bare
// <img src>/<a download>: auth is the HttpOnly portal cookie, and only
// the client path gets the 401 → silent-refresh → retry treatment —
// see AuthedImage for the full why.

import { useRef, useState } from "react";

import { Download } from "lucide-react";

import { api } from "@/lib/api";
import { formatBytes, isImageMime } from "@/lib/fileUtil";
import type { FileAttachment } from "@/lib/types";
import { openLightbox } from "@/components/ui/ImageLightbox";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { AuthedImage } from "./AuthedImage";
import { VideoPlayer } from "./VideoPlayer";

interface Props {
  files: FileAttachment[];
}

const isVideoMime = (ct?: string) => Boolean(ct?.startsWith("video/"));

export function MessageAttachments({ files }: Props) {
  if (!files || files.length === 0) return null;
  const videos = files.filter((f) => isVideoMime(f.content_type));
  const images = files.filter((f) => isImageMime(f.content_type));
  const others = files.filter(
    (f) => !isImageMime(f.content_type) && !isVideoMime(f.content_type),
  );
  return (
    <div className="mt-1 flex flex-col gap-1.5">
      {videos.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {videos.map((f) => (
            <VideoPlayer key={f.id} fileId={f.id} filename={f.filename} />
          ))}
        </div>
      ) : null}
      {images.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {images.map((f) => (
            <ImageThumb key={f.id} file={f} />
          ))}
        </div>
      ) : null}
      {others.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {others.map((f) => (
            <FileCard key={f.id} file={f} />
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

// Non-image attachment → a clean download card (icon + name + size +
// download affordance). Download rides the cookie-authed blob fetch
// (api.downloadFile), whose 401 → refresh → retry survives
// portal_access_token expiry where a bare <a download> could not.
function FileCard({ file }: { file: FileAttachment }) {
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
      className="focus-ring flex w-full max-w-[300px] items-center gap-3 rounded-lg border border-border bg-bg-secondary p-3 text-left transition-colors hover:border-border-strong hover:bg-bg-elevated disabled:opacity-60"
      title={`Download ${file.filename}`}
    >
      <FileTypeBadge
        filename={file.filename}
        contentType={file.content_type}
        size="md"
        className="shrink-0"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-text-primary">
          {file.filename}
        </span>
        <span className="block text-xs text-text-muted">
          {formatBytes(file.file_size_bytes)}
        </span>
      </span>
      <Download className="h-4 w-4 shrink-0 text-text-muted" aria-hidden />
    </button>
  );
}
