"use client";

// Compact "📎 N files" affordance pinned to the conversation header.
// Click → dropdown listing every file in the conversation with a
// download link. Fetches on open so we don't pay for the round-trip
// on conversations that have no attachments yet.

import { useEffect, useRef, useState } from "react";

import { useT } from "@/lib/i18n";

import { api } from "@/lib/api";
import { formatBytes, isImageMime } from "@/lib/fileUtil";
import type { FileAttachment } from "@/lib/types";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { AuthedImage } from "./AuthedImage";

interface Props {
  conversationId: string;
}

export function ConversationFiles({ conversationId }: Props) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<FileAttachment[] | null>(null);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Initial-load: a lightweight HEAD-style fetch on mount so the
  // header chip knows whether to render at all. The full list
  // re-fetches on every open so newly-uploaded files surface even
  // without a page reload.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await api.listFiles(conversationId);
        if (!cancelled) setFiles(res.files);
      } catch {
        if (!cancelled) setFiles([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  async function refetch() {
    setLoading(true);
    try {
      const res = await api.listFiles(conversationId);
      setFiles(res.files);
    } finally {
      setLoading(false);
    }
  }

  const count = files?.length ?? 0;
  if (count === 0) return null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          if (!open) void refetch();
        }}
        className="focus-ring inline-flex items-center gap-1 rounded-full border border-border bg-bg-secondary px-2 py-0.5 text-[11px] text-text-secondary hover:text-text-primary"
        aria-label={t("convfiles.aria", { n: String(count) })}
        title={t("convfiles.aria", { n: String(count) })}
      >
        <span aria-hidden>📎</span>
        <span>{count} file{count === 1 ? "" : "s"}</span>
      </button>
      {open ? (
        <div className="popover-appear absolute right-0 top-full z-20 mt-1 w-72 overflow-hidden rounded-lg border border-border bg-bg-surface shadow-2xl">
          <div className="border-b border-border/60 px-3 py-2 text-[10px] uppercase tracking-wider text-text-muted">
            {t("convfiles.header")}
          </div>
          <div className="scrollbar-thin max-h-72 overflow-y-auto py-1">
            {loading ? (
              <div className="px-3 py-3 text-center text-[11px] text-text-muted">
                Loading…
              </div>
            ) : files && files.length > 0 ? (
              files.map((f) => <FileRow key={f.id} file={f} />)
            ) : (
              <div className="px-3 py-3 text-center text-[11px] text-text-muted">
                {t("convfiles.empty")}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// One row in the files popup. Images show a real (authenticated)
// thumbnail; everything else shows the monochrome FileTypeBadge.
// Download goes through api.downloadFile — cookie-authed with the
// 401 → silent-refresh → retry contract (a bare <a download> broke
// the moment the access cookie lapsed).
function FileRow({ file }: { file: FileAttachment }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const isImage = isImageMime(file.content_type);
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
      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-bg-elevated disabled:opacity-60"
      title={t("file.downloadTitle", { name: file.filename })}
    >
      {isImage ? (
        <AuthedImage
          fileId={file.id}
          alt={file.filename}
          className="h-9 w-9 shrink-0 rounded object-cover"
        />
      ) : (
        <FileTypeBadge
          filename={file.filename}
          contentType={file.content_type}
          size="md"
          className="shrink-0"
        />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-text-primary">
          {file.filename}
        </span>
        <span className="block text-[10px] text-text-muted">
          {formatBytes(file.file_size_bytes)}
        </span>
      </span>
      <svg
        width={12}
        height={12}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
        className="shrink-0 text-text-muted"
      >
        <path
          d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
