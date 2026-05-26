"use client";

// Compact "📎 N files" affordance pinned to the conversation header.
// Click → dropdown listing every file in the conversation with a
// download link. Fetches on open so we don't pay for the round-trip
// on conversations that have no attachments yet.

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import {
  formatBytes,
  iconEmojiFor,
  iconKindFor,
} from "@/lib/fileUtil";
import type { FileAttachment } from "@/lib/types";

interface Props {
  conversationId: string;
}

export function ConversationFiles({ conversationId }: Props) {
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
        aria-label={`Conversation files (${count})`}
        title={`Conversation files (${count})`}
      >
        <span aria-hidden>📎</span>
        <span>{count} file{count === 1 ? "" : "s"}</span>
      </button>
      {open ? (
        <div className="popover-appear absolute right-0 top-full z-20 mt-1 w-72 overflow-hidden rounded-lg border border-border bg-bg-surface shadow-2xl">
          <div className="border-b border-border/60 px-3 py-2 text-[10px] uppercase tracking-wider text-text-muted">
            Files in this conversation
          </div>
          <div className="scrollbar-thin max-h-72 overflow-y-auto py-1">
            {loading ? (
              <div className="px-3 py-3 text-center text-[11px] text-text-muted">
                Loading…
              </div>
            ) : files && files.length > 0 ? (
              files.map((f) => (
                <a
                  key={f.id}
                  href={api.downloadUrl(f.id)}
                  download={f.filename}
                  className="flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-bg-elevated"
                  title={`Download ${f.filename}`}
                >
                  <span aria-hidden className="text-lg leading-none">
                    {iconEmojiFor(iconKindFor(f.filename, f.content_type))}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-text-primary">
                      {f.filename}
                    </span>
                    <span className="block text-[10px] text-text-muted">
                      {formatBytes(f.file_size_bytes)}
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
                </a>
              ))
            ) : (
              <div className="px-3 py-3 text-center text-[11px] text-text-muted">
                No files attached.
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
