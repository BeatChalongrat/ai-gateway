"use client";

// Renders the file badges + inline image thumbnails that hang under
// a user message. Images get an inline preview that opens in the
// global lightbox on click; everything else gets a download pill.

import { api } from "@/lib/api";
import {
  formatBytes,
  iconEmojiFor,
  iconKindFor,
  isImageMime,
} from "@/lib/fileUtil";
import type { FileAttachment } from "@/lib/types";
import { openLightbox } from "@/components/ui/ImageLightbox";

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
  const url = api.downloadUrl(file.id);
  return (
    <button
      type="button"
      onClick={() => openLightbox(url, file.filename)}
      className="focus-ring overflow-hidden rounded-md border border-border bg-bg-base"
      title={`${file.filename} · ${formatBytes(file.file_size_bytes)}`}
    >
      <img
        src={url}
        alt={file.filename}
        loading="lazy"
        decoding="async"
        className="block max-h-[200px] max-w-[300px] object-cover"
      />
    </button>
  );
}

function FileBadge({ file }: { file: FileAttachment }) {
  const kind = iconKindFor(file.filename, file.content_type);
  return (
    <a
      href={api.downloadUrl(file.id)}
      download={file.filename}
      className="focus-ring inline-flex max-w-[260px] items-center gap-2 rounded-md border border-border bg-bg-secondary px-2 py-1.5 text-[11px] text-text-secondary transition-colors hover:border-border-strong hover:bg-bg-elevated hover:text-text-primary"
      title={`Download ${file.filename}`}
    >
      <span aria-hidden className="text-base leading-none">
        {iconEmojiFor(kind)}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
        {file.filename}
      </span>
      <span className="shrink-0 text-text-muted">
        {formatBytes(file.file_size_bytes)}
      </span>
    </a>
  );
}
