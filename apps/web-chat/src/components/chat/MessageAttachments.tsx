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
import { downloadFromObjectUrl, formatBytes, isImageMime } from "@/lib/fileUtil";
import { useT } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import { openLightbox } from "@/components/ui/ImageLightbox";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { AuthedImage } from "./AuthedImage";
import { FilePreviewModal } from "./FilePreviewModal";
import { VideoPlayer } from "./VideoPlayer";

interface Props {
  files: FileAttachment[];
}

const isVideoMime = (ct?: string) => Boolean(ct?.startsWith("video/"));

export function MessageAttachments({ files }: Props) {
  // Phase 10 — one preview overlay for the whole attachment group, so
  // only one file can be open at a time and the modal state does not
  // live inside each card.
  // FIX76 — on the conversation page the preview opens in the docked
  // side pane (page state, sidePaneStore); the local modal is the
  // fallback for surfaces without the slot (quick-ask, share view).
  const hostMounted = useSidePaneStore((s) => s.hostMounted);
  const openPane = useSidePaneStore((s) => s.open);
  const [previewing, setPreviewing] = useState<FileAttachment | null>(null);
  const preview = (f: FileAttachment) => {
    if (hostMounted) openPane({ kind: "preview", file: f });
    else setPreviewing(f);
  };

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
            <div key={f.id} className="flex flex-col gap-0.5">
              <VideoPlayer fileId={f.id} filename={f.filename} />
              {f.generated_by ? <MadeBy by={f.generated_by} /> : null}
            </div>
          ))}
        </div>
      ) : null}
      {images.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {images.map((f) => (
            <figure key={f.id} className="m-0 flex flex-col gap-0.5">
              <ImageThumb file={f} />
              {f.generated_by ? <MadeBy by={f.generated_by} /> : null}
            </figure>
          ))}
        </div>
      ) : null}
      {others.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {others.map((f) => (
            <FileCard key={f.id} file={f} onPreview={() => preview(f)} />
          ))}
        </div>
      ) : null}

      {previewing ? (
        <FilePreviewModal
          file={previewing}
          onClose={() => setPreviewing(null)}
        />
      ) : null}
    </div>
  );
}

// FIX67 — attribution under a generated image/video. Shown every time (not
// only after a fallback) so a user has a basis to compare: a picture from a
// different model is a different thing (style, cost, quality). i18n TH/EN.
function MadeBy({ by }: { by: string }) {
  const t = useT();
  return (
    <figcaption className="text-[11px] text-text-muted" data-testid="made-by">
      {t("chat.media.madeBy", { model: by })}
    </figcaption>
  );
}

function ImageThumb({ file }: { file: FileAttachment }) {
  const t = useT();
  // AuthedImage hands us the loaded object URL; we reuse it for the
  // full-size lightbox AND the download button so neither triggers a
  // second fetch. The thumb stays mounted while the lightbox overlay
  // is open, so the URL is still valid (AuthedImage revokes only on
  // unmount).
  const objectUrl = useRef<string | null>(null);
  return (
    <div className="group relative">
      <button
        type="button"
        onClick={() => {
          if (objectUrl.current)
            openLightbox(objectUrl.current, file.filename, file.filename);
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
      {/* Hover download — W2. Sibling of the thumb button (a nested
          button would be invalid HTML); saves straight from the loaded
          object URL, zero re-fetch. Focus reveals it for keyboard use. */}
      <button
        type="button"
        aria-label={t("common.download")}
        title={t("common.download")}
        data-testid="image-thumb-download"
        onClick={() => {
          if (objectUrl.current)
            downloadFromObjectUrl(objectUrl.current, file.filename);
        }}
        className="focus-ring absolute right-1.5 top-1.5 rounded-md bg-black/55 p-1.5 text-white opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Download className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}

// Non-image attachment → a clean download card (icon + name + size +
// download affordance). Download rides the cookie-authed blob fetch
// (api.downloadFile), whose 401 → refresh → retry survives
// portal_access_token expiry where a bare <a download> could not.
function FileCard({
  file,
  onPreview,
}: {
  file: FileAttachment;
  onPreview: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex w-full max-w-[300px] items-center gap-1 rounded-lg border border-border bg-bg-secondary pr-1 transition-colors hover:border-border-strong hover:bg-bg-elevated">
      <button
        type="button"
        onClick={onPreview}
        data-testid="file-card-preview"
        className="focus-ring flex min-w-0 flex-1 items-center gap-3 p-3 text-left"
        title={file.filename}
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
      </button>
      <button
        type="button"
        disabled={busy}
        aria-label={`Download ${file.filename}`}
        data-testid="file-card-download"
        onClick={async () => {
          setBusy(true);
          try {
            await api.downloadFile(file.id, file.filename);
          } finally {
            setBusy(false);
          }
        }}
        className="focus-ring shrink-0 rounded-md p-2 text-text-muted transition-colors hover:bg-bg-surface hover:text-text-primary disabled:opacity-60"
      >
        <Download className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
