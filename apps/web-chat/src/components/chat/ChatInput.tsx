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
const EMPTY_PENDING_FILES = [] as const;

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
    conversationId
      ? s.pendingFiles[conversationId] ?? EMPTY_PENDING_FILES
      : EMPTY_PENDING_FILES,
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
      className="safe-bottom bg-bg-base px-3 py-3 md:px-6 md:py-4"
      style={
        keyboardInset > 0
          ? { transform: `translateY(-${keyboardInset}px)`, transition: "transform 150ms ease-out" }
          : undefined
      }
    >
      <div className="mx-auto flex w-full max-w-chat flex-col gap-2">
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
                        removePendingFile(conversationId, p.id);
                        void uploadOne(conversationId, p.file);
                      }
                    : undefined
                }
              />
            ))}
          </div>
        ) : null}

        {/* Composer pill */}
        <div
          className="flex flex-col gap-1.5 border border-border bg-bg-surface px-2 pb-1.5 pt-2 transition-colors focus-within:border-border-strong"
          style={{
            borderRadius: 24,
            boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)",
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={FILE_PICKER_ACCEPT}
            multiple
            className="hidden"
            onChange={onPickFiles}
          />

          {/* Top row: paperclip + textarea + send/stop */}
          <div className="flex items-end gap-1">
            {conversationId ? (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                aria-label="Attach files"
                title="Attach files"
                disabled={streaming || disabled}
                className="focus-ring tap-44 shrink-0 rounded-full p-2 text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
              >
                <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M12 5v14M5 12h14"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            ) : null}
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
              className="flex-1 resize-none bg-transparent px-1 py-2 text-[15px] text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-60"
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
                className={`focus-ring absolute inset-0 flex items-center justify-center rounded-full bg-status-danger/85 text-white transition-all duration-150 hover:bg-status-danger ${
                  streaming ? "scale-100 opacity-100" : "pointer-events-none scale-90 opacity-0"
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
                className={`focus-ring absolute inset-0 flex items-center justify-center rounded-full text-white transition-all duration-150 disabled:cursor-not-allowed ${
                  streaming ? "pointer-events-none scale-90 opacity-0" : "scale-100 opacity-100"
                } ${
                  canSend
                    ? "bg-accent hover:bg-accent-hover"
                    : "bg-bg-elevated text-text-muted"
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

          {/* Bottom row: model selector pill */}
          <div className="flex items-center gap-2 pl-1">
            <ModelSelector
              activeModelId={activeModelId}
              disabled={streaming}
              compact
              onPick={onModelPick ? (m) => onModelPick(m.id) : undefined}
            />
          </div>
        </div>

        <p className="text-center text-[11px] text-text-muted">
          SRAN Chat can make mistakes. Check important info.
        </p>
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
