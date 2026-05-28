"use client";

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import {
  FILE_PICKER_ACCEPT,
  MAX_FILES_PER_MESSAGE,
  validateFile,
} from "@/lib/fileUtil";
import { useT } from "@/lib/i18n";
import { toast } from "@/components/ui/Toast";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { useWhisperInput } from "@/hooks/useWhisperInput";
import { useChatStore } from "@/stores/chatStore";
import { useVoiceStore } from "@/stores/voiceStore";

import { FilePreviewCard } from "./FilePreviewCard";
import { KBSelector } from "./KBSelector";
import { ModelSelector } from "./ModelSelector";
import { RecordingBar } from "./RecordingBar";

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
  /** Currently-bound KB id (null when no KB is selected). When the
   *  conversation has one bound, the composer renders the KB pill
   *  next to the model picker. */
  activeKBId?: string | null;
  onKBPick?: (kbId: string | null) => Promise<void>;
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
  activeKBId,
  onKBPick,
  onSend,
  onStop,
  injectedText,
  onInjectedTextConsumed,
}: Props) {
  const t = useT();
  const [text, setText] = useState("");
  const [isMobile, setIsMobile] = useState(false);
  // Distance (px) the on-screen keyboard is occluding the bottom of
  // the layout viewport. Updated from visualViewport.resize so the
  // composer floats just above the keyboard on iOS / Android.
  const [keyboardInset, setKeyboardInset] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Voice input ────────────────────────────────────────────
  // Two hooks because the user can flip the input method in voice
  // settings; we always wire both and dispatch based on the active
  // method at start-time. Browser support flags guard the toggle so
  // unsupported paths fall back automatically.
  const voiceInputMethod = useVoiceStore((s) => s.inputMethod);
  const isRecording = useVoiceStore((s) => s.isRecording);
  const voiceTranscript = useVoiceStore((s) => s.transcript);
  const voiceInterim = useVoiceStore((s) => s.interimTranscript);
  const voiceError = useVoiceStore((s) => s.error);
  const recordingDuration = useVoiceStore((s) => s.recordingDuration);
  const setRecordingDuration = useVoiceStore((s) => s.setRecordingDuration);
  const resetVoiceTranscript = useVoiceStore((s) => s.resetTranscript);
  const setVoiceError = useVoiceStore((s) => s.setError);
  const webSpeech = useVoiceInput();
  const whisper = useWhisperInput();
  // Pick whichever hook backs the active method; fall back to the
  // other if the chosen one isn't supported in this browser.
  const useWhisperPath =
    voiceInputMethod === "whisper" ||
    (voiceInputMethod === "web-speech" && !webSpeech.isSupported);
  const voiceSupported = useWhisperPath ? whisper.isSupported : webSpeech.isSupported;
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

  // Run the recording-duration counter while isRecording. setInterval
  // is preferred over requestAnimationFrame here — we render once per
  // second, not once per frame, and the duration only updates the
  // chip text so the cost is bounded.
  useEffect(() => {
    if (!isRecording) {
      setRecordingDuration(0);
      return;
    }
    const startedAt = Date.now();
    const id = window.setInterval(() => {
      setRecordingDuration(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [isRecording, setRecordingDuration]);

  // Surface voice errors as toasts. Reset the store error after we
  // surface it so the same message doesn't pop twice on re-render.
  useEffect(() => {
    if (!voiceError) return;
    toast(voiceError);
    setVoiceError(null);
  }, [voiceError, setVoiceError]);

  // When the recording stops AND a final transcript came back, merge
  // it into the textarea. We append (with a separator) so any
  // typed-then-dictated combo doesn't blow away the user's earlier
  // input.
  useEffect(() => {
    if (isRecording) return;
    if (!voiceTranscript.trim()) return;
    setText((cur) => {
      const sep = cur && !cur.endsWith(" ") ? " " : "";
      return cur + sep + voiceTranscript.trim();
    });
    resetVoiceTranscript();
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) {
        ta.focus();
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      }
    });
  }, [isRecording, voiceTranscript, resetVoiceTranscript]);

  const startVoice = () => {
    // Optional haptic on mobile — supported on Android Chrome.
    if (typeof navigator !== "undefined" && "vibrate" in navigator) {
      try {
        (navigator as Navigator & { vibrate?: (p: number | number[]) => boolean }).vibrate?.(10);
      } catch {
        /* nothing */
      }
    }
    if (useWhisperPath) {
      void whisper.start();
    } else {
      webSpeech.start();
    }
  };

  const stopVoice = () => {
    if (typeof navigator !== "undefined" && "vibrate" in navigator) {
      try {
        (navigator as Navigator & { vibrate?: (p: number | number[]) => boolean }).vibrate?.(10);
      } catch {
        /* nothing */
      }
    }
    if (useWhisperPath) {
      void whisper.stop();
    } else {
      webSpeech.stop();
    }
  };

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

  // Clipboard paste → upload (Phase 4 Session 4 vision flow).
  // Captures any image item in a paste event and runs it through
  // the same handleFiles pipeline pickers use. preventDefault only
  // fires when an image is present so plain-text paste into the
  // textarea still works. Listens at the textarea so paste only
  // fires when the composer is focused.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const el = taRef.current;
    if (!el || !conversationId) return;
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items || items.length === 0) return;
      const images: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file" && item.type.startsWith("image/")) {
          const f = item.getAsFile();
          if (f) images.push(f);
        }
      }
      if (images.length === 0) return;
      e.preventDefault();
      void handleFiles(images);
    };
    el.addEventListener("paste", onPaste);
    return () => el.removeEventListener("paste", onPaste);
  }, [conversationId]);

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
        {/* Recording mode replaces the composer pill with a
            full-width recording bar. Same outer container — the
            decision is purely render-level so flex/scroll constraints
            from the parent don't shift. */}
        {isRecording || whisper.isTranscribing ? (
          <RecordingBar
            transcript={voiceTranscript}
            interimTranscript={voiceInterim}
            elapsedSec={recordingDuration}
            isTranscribing={whisper.isTranscribing}
            stream={null}
            onStop={stopVoice}
          />
        ) : null}

        {/* Composer pill — Claude-style.
            Pending files (when any) sit at the top of the pill so the
            container visibly grows when the user attaches something.
            Row textarea → row toolbar follows underneath.
            Decoupling the buttons from the textarea row sidesteps
            every "send is misaligned with first line" problem — the
            send button now sits in its own row with its own padding
            and only competes with the model pill for vertical
            alignment. */}
        <div
          className="flex flex-col border border-border bg-bg-surface transition-colors focus-within:border-border-strong"
          style={{
            borderRadius: 24,
            boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)",
            display: isRecording || whisper.isTranscribing ? "none" : undefined,
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

          {/* Row 0: pending-file chips. Wraps to a second line rather
              than scrolling horizontally — feels native to the pill. */}
          {pendingFiles.length > 0 ? (
            <div className="flex flex-wrap gap-2 px-3 pt-3">
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

          {/* Row 1: textarea, full width */}
          <div className="px-4 pt-3">
            <textarea
              ref={taRef}
              rows={1}
              value={text}
              disabled={disabled}
              placeholder={
                streaming
                  ? t("input.placeholder.streaming")
                  : isUploading
                    ? t("input.placeholder.uploading")
                    : t("input.placeholder")
              }
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKeyDown}
              className="w-full resize-none bg-transparent text-[15px] leading-relaxed text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-60"
              style={{
                maxHeight: maxTextareaHeight,
                transition: "height 100ms ease-out",
              }}
            />
          </div>

          {/* Row 2: attach (left) · model + send (right) */}
          <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
            {/* Left — attach button. The plus mark replaces the
                paperclip per the Claude reference; same behaviour. */}
            <div className="flex items-center">
              {conversationId ? (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  aria-label={t("input.attach")}
                  title={t("input.attach")}
                  disabled={streaming || disabled}
                  className="focus-ring tap-44 inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
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
            </div>

            {/* Right — KB selector + model selector pill + send/stop. */}
            <div className="flex items-center gap-1.5">
              {onKBPick ? (
                <KBSelector
                  conversationId={conversationId}
                  activeKBId={activeKBId ?? null}
                  onPick={onKBPick}
                  disabled={streaming}
                />
              ) : null}
              <ModelSelector
                activeModelId={activeModelId}
                disabled={streaming}
                compact
                onPick={onModelPick ? (m) => onModelPick(m.id) : undefined}
              />
              {voiceSupported && !streaming ? (
                <button
                  type="button"
                  onClick={startVoice}
                  aria-label={t("input.voice")}
                  title={t("input.voice")}
                  disabled={disabled}
                  className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <rect x="9" y="3" width="6" height="11" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                  </svg>
                </button>
              ) : null}
              {streaming ? (
                <button
                  type="button"
                  onClick={onStop}
                  aria-label={t("input.stop")}
                  className="focus-ring tap-44 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-status-danger/85 text-white transition-colors duration-150 hover:bg-status-danger"
                >
                  <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={submit}
                  disabled={!canSend}
                  aria-label={t("input.send")}
                  // ChatGPT-style send button: when something is typed,
                  // the circle inverts to the foreground colour (white
                  // in dark mode, near-black in light mode) so the icon
                  // pops without using the accent green. The disabled
                  // state stays muted on the same neutral surface as
                  // the rest of the composer chrome.
                  //
                  // Inline styles reference the RAW CSS variables that
                  // ``globals.css`` defines under [data-theme="dark"]
                  // and the light-mode root. ``--bg-base`` /
                  // ``--bg-elevated`` only exist as Tailwind aliases
                  // (see tailwind.config.js), so we use ``--bg-primary``
                  // and ``--bg-surface-hover`` here.
                  className="focus-ring tap-44 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors duration-150 disabled:cursor-not-allowed"
                  style={
                    canSend
                      ? {
                          background: "var(--text-primary)",
                          color: "var(--bg-primary)",
                        }
                      : {
                          background: "var(--bg-surface-hover)",
                          color: "var(--text-tertiary)",
                        }
                  }
                >
                  <svg
                    width={16}
                    height={16}
                    viewBox="0 0 16 16"
                    fill="none"
                    aria-hidden
                  >
                    <path
                      d="M8 14V2M8 2L3 7M8 2L13 7"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="text-center text-[11px] text-text-muted">
          {t("input.footer")}
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
