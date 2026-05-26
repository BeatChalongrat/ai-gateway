"use client";

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import {
  FILE_PICKER_ACCEPT,
  MAX_FILES_PER_MESSAGE,
  validateFile,
} from "@/lib/fileUtil";
import { toast } from "@/components/ui/Toast";
import { useChatStore } from "@/stores/chatStore";

import { FilePreviewCard } from "./FilePreviewCard";
import { ModelSelector } from "./ModelSelector";

interface Props {
  /** Active conversation id. The input needs it for the pending-
   *  files store + to call the upload endpoint. Pass null on the
   *  landing page (no conv yet); the paperclip + drop zone are
   *  hidden when null. */
  conversationId: string | null;
  disabled: boolean;
  streaming: boolean;
  activeModelId?: string | null;
  onModelPick?: (modelId: string) => void;
  onSend: (text: string, fileIds: string[]) => void;
  onStop: () => void;
  injectedText?: string | null;
  onInjectedTextConsumed?: () => void;
}

// 6 lines × ~24px on desktop; trimmed to 4 lines on phones so the
// keyboard + bubble never crowd the viewport at the same time.
const MAX_TEXTAREA_HEIGHT_DESKTOP = 180;
const MAX_TEXTAREA_HEIGHT_MOBILE = 120;
const MAX_CONCURRENT_UPLOADS = 3;

export function ChatInput({
  conversationId,
  disabled,
  streaming,
  activeModelId,
  onModelPick,
  onSend,
  onStop,
  injectedText,
  onInjectedTextConsumed,
}: Props) {
  const [text, setText] = useState("");
  const [isMobile, setIsMobile] = useState(false);
  // Distance (px) the on-screen keyboard is occluding the bottom of
  // the layout viewport. Updated from visualViewport.resize so the
  // composer floats just above the keyboard on iOS / Android.
  const [keyboardInset, setKeyboardInset] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const maxTextareaHeight = isMobile
    ? MAX_TEXTAREA_HEIGHT_MOBILE
    : MAX_TEXTAREA_HEIGHT_DESKTOP;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia("(max-width: 639px)");
    const sync = () => setIsMobile(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  // visualViewport tracks the layout-viewport overlap with the
  // soft keyboard on mobile. When it shrinks, the difference vs
  // window.innerHeight is the keyboard overlay height — push the
  // composer up by that amount so the textarea stays visible.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    if (!vv) return;
    const sync = () => {
      const overlap = window.innerHeight - vv.height - vv.offsetTop;
      setKeyboardInset(Math.max(0, overlap));
    };
    sync();
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
    };
  }, []);
  const pendingFiles = useChatStore((s) =>
    conversationId ? s.pendingFiles[conversationId] ?? [] : [],
  );
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const updateFileProgress = useChatStore((s) => s.updateFileProgress);
  const markFileReady = useChatStore((s) => s.markFileReady);
  const markFileError = useChatStore((s) => s.markFileError);
  const removePendingFile = useChatStore((s) => s.removePendingFile);
  const clearPendingFiles = useChatStore((s) => s.clearPendingFiles);

  // Auto-grow up to the device's max line count.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, maxTextareaHeight)}px`;
  }, [text, maxTextareaHeight]);

  useEffect(() => {
    if (!injectedText) return;
    setText(injectedText);
    onInjectedTextConsumed?.();
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) {
        ta.focus();
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      }
    });
  }, [injectedText, onInjectedTextConsumed]);

  const isUploading = pendingFiles.some((p) => p.status === "uploading");
  const readyFileIds = pendingFiles
    .filter((p) => p.status === "ready" && p.serverId)
    .map((p) => p.serverId!);
  const canSend =
    !disabled &&
    !streaming &&
    !isUploading &&
    (text.trim().length > 0 || readyFileIds.length > 0);

  function submit() {
    if (!canSend) return;
    const t = text.trim();
    onSend(t, readyFileIds);
    setText("");
    if (conversationId) clearPendingFiles(conversationId);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  // ── File upload pipeline ─────────────────────────────────
  async function uploadOne(convId: string, file: File) {
    const pendingId = addPendingFile(convId, file);
    try {
      const row = await api.uploadFile(convId, file, {
        onProgress: (p) => updateFileProgress(convId, pendingId, p),
      });
      markFileReady(convId, pendingId, row.id);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : "Upload failed. Try again.";
      markFileError(convId, pendingId, msg);
      toast(msg);
    }
  }

  async function handleFiles(filesRaw: File[]) {
    if (!conversationId) return;
    // Per-message cap. Existing valid pending + new candidates.
    const existing = pendingFiles.filter((p) => p.status !== "error").length;
    const slots = Math.max(0, MAX_FILES_PER_MESSAGE - existing);
    if (slots === 0) {
      toast(`At most ${MAX_FILES_PER_MESSAGE} files per message.`);
      return;
    }

    const accepted: File[] = [];
    for (const f of filesRaw) {
      if (accepted.length === slots) {
        toast(`Only ${slots} more file(s) can attach to this message.`);
        break;
      }
      const v = validateFile(f);
      if (!v.ok) {
        toast(`${f.name}: ${v.reason}`);
        continue;
      }
      accepted.push(f);
    }
    if (accepted.length === 0) return;

    // Parallel upload, max 3 concurrent. Roll our own little
    // bounded executor — simpler than pulling p-limit.
    let cursor = 0;
    const workers: Promise<void>[] = [];
    for (let i = 0; i < Math.min(MAX_CONCURRENT_UPLOADS, accepted.length); i++) {
      workers.push(
        (async () => {
          while (cursor < accepted.length) {
            const idx = cursor++;
            await uploadOne(conversationId, accepted[idx]);
          }
        })(),
      );
    }
    await Promise.all(workers);
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    const files = Array.from(list);
    e.target.value = ""; // allow re-picking the same file later
    void handleFiles(files);
  }

  return (
    <div
      className="safe-bottom border-t border-border bg-bg-base/95 px-4 py-3 md:px-10 md:py-4"
      style={
        keyboardInset > 0
          ? { transform: `translateY(-${keyboardInset}px)`, transition: "transform 150ms ease-out" }
          : undefined
      }
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {/* Pending-file preview row */}
        {pendingFiles.length > 0 ? (
          <div className="scrollbar-thin flex gap-2 overflow-x-auto py-1">
            {pendingFiles.map((p) => (
              <FilePreviewCard
                key={p.id}
                pending={p}
                onRemove={() => {
                  if (conversationId) removePendingFile(conversationId, p.id);
                }}
                onRetry={
                  p.status === "error" && conversationId
                    ? () => {
                        // Re-upload: remove old card and start fresh.
                        removePendingFile(conversationId, p.id);
                        void uploadOne(conversationId, p.file);
                      }
                    : undefined
                }
              />
            ))}
          </div>
        ) : null}

        <div className="flex items-end gap-2 rounded-2xl border border-border bg-bg-secondary px-3 py-2 transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={FILE_PICKER_ACCEPT}
            multiple
            className="hidden"
            onChange={onPickFiles}
          />

          {/* Paperclip — only shown inside an active conversation */}
          {conversationId ? (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Attach files"
              title="Attach files"
              disabled={streaming || disabled}
              className="focus-ring tap-44 shrink-0 rounded-md p-1.5 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M21 12.5l-8.59 8.59a5.5 5.5 0 0 1-7.78-7.78l9.19-9.19a3.67 3.67 0 0 1 5.19 5.19L9.83 18.49a1.83 1.83 0 1 1-2.6-2.6l8.49-8.48"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          ) : null}

          <div className="pb-0.5">
            <ModelSelector
              activeModelId={activeModelId}
              disabled={streaming}
              compact
              onPick={onModelPick ? (m) => onModelPick(m.id) : undefined}
            />
          </div>
          <textarea
            ref={taRef}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={
              streaming
                ? "Streaming response…"
                : isUploading
                  ? "Uploading…"
                  : "Message SRAN Chat…"
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            className="flex-1 resize-none bg-transparent px-1 py-1.5 text-base text-text-primary placeholder:text-text-muted focus:outline-none disabled:opacity-60 md:text-sm"
            style={{
              maxHeight: maxTextareaHeight,
              transition: "height 100ms ease-out",
            }}
          />
          <div className="tap-44 relative flex h-9 w-9 shrink-0 items-center justify-center">
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generation"
              tabIndex={streaming ? 0 : -1}
              className={`focus-ring absolute inset-0 flex items-center justify-center rounded-lg bg-status-danger/85 text-white transition-all duration-150 hover:bg-status-danger ${
                streaming
                  ? "scale-100 opacity-100"
                  : "pointer-events-none scale-90 opacity-0"
              }`}
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={!canSend}
              aria-label="Send"
              tabIndex={streaming ? -1 : 0}
              className={`focus-ring absolute inset-0 flex items-center justify-center rounded-lg bg-accent text-white transition-all duration-150 hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40 ${
                streaming
                  ? "pointer-events-none scale-90 opacity-0"
                  : "scale-100 opacity-100"
              }`}
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M12 19V5M5 12l7-7 7 7"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
        <div className="text-center text-[10px] text-text-muted">
          Press <kbd className="rounded bg-bg-elevated px-1">Enter</kbd> to send,{" "}
          <kbd className="rounded bg-bg-elevated px-1">Shift</kbd>+
          <kbd className="rounded bg-bg-elevated px-1">Enter</kbd> for new line.
        </div>
      </div>
    </div>
  );
}

// Imperative API for the drag-and-drop wrapper. The conversation
// page's drop handler calls this directly rather than re-implementing
// validation.
export async function uploadFiles(
  convId: string,
  files: File[],
  store: {
    addPendingFile: (id: string, f: File) => string;
    updateFileProgress: (id: string, pid: string, p: number) => void;
    markFileReady: (id: string, pid: string, sid: string) => void;
    markFileError: (id: string, pid: string, err: string) => void;
  },
): Promise<void> {
  for (const f of files) {
    const v = validateFile(f);
    if (!v.ok) {
      toast(`${f.name}: ${v.reason}`);
      continue;
    }
    const pid = store.addPendingFile(convId, f);
    try {
      const row = await api.uploadFile(convId, f, {
        onProgress: (p) => store.updateFileProgress(convId, pid, p),
      });
      store.markFileReady(convId, pid, row.id);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Upload failed.";
      store.markFileError(convId, pid, msg);
      toast(msg);
    }
  }
}
