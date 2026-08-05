"use client";

import { useEffect, useRef, useState } from "react";

import { Clapperboard, ImagePlus } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { ExtractionStatus } from "@/lib/fileExtraction";
import { isExtracting, watchExtraction } from "@/lib/fileExtraction";
import {
  FILE_PICKER_ACCEPT,
  MAX_FILES_PER_MESSAGE,
  validateFile,
} from "@/lib/fileUtil";
import { tOutside, useT } from "@/lib/i18n";
import { toast } from "@/components/ui/Toast";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { useWhisperInput } from "@/hooks/useWhisperInput";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";
import { useVoiceStore } from "@/stores/voiceStore";

import { FilePreviewCard } from "./FilePreviewCard";
import { KBSelector } from "./KBSelector";
import { ModelSelector } from "./ModelSelector";
import { RecordingBar } from "./RecordingBar";
import { AudioGenForm } from "../media/AudioGenForm";
import { MediaModeIndicator } from "../media/MediaModeIndicator";
import { VideoGenForm } from "../media/VideoGenForm";

interface Props {
  /** Active conversation id. The input uses it as the key for the
   *  pending-files store + to call the upload endpoint. Pass null
   *  on the landing page; in that mode the input calls
   *  `onRequestConversation` before any upload-related action so a
   *  conversation row is eager-created on first attach/paste/drop. */
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
  onSend: (
    text: string,
    fileIds: string[],
    imageMode?: boolean,
    videoMode?: boolean,
  ) => void;
  onStop: () => void;
  injectedText?: string | null;
  onInjectedTextConsumed?: () => void;
  /** Lazy conversation provider. ChatInput calls this whenever an
   *  upload-related action (attach click, drag-drop, paste image)
   *  fires without a `conversationId` in props. The landing page
   *  wires it to a function that eager-creates a conversation, sets
   *  its local state, and returns the new id — so the next render
   *  of ChatInput receives a real conversationId and the existing
   *  pending-files plumbing takes over. Returning null aborts the
   *  upload silently. */
  onRequestConversation?: () => Promise<string | null>;
}

// 6 lines × ~24px on desktop; trimmed to 4 lines on phones so the
// keyboard + bubble never crowd the viewport at the same time.
const MAX_TEXTAREA_HEIGHT_DESKTOP = 180;
const MAX_TEXTAREA_HEIGHT_MOBILE = 120;
const MAX_CONCURRENT_UPLOADS = 3;
const EMPTY_PENDING_FILES = [] as const;
// Sentinel bucket key used to stage files BEFORE a conversation id
// exists (landing-page attach flow). Once ensureConv resolves, the
// rows migrate from this key onto the real conv id so the existing
// pendingFiles[realConvId] selector keeps rendering them.
const DRAFT_CONV_KEY = "__chat_input_draft__";

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
  onRequestConversation,
}: Props) {
  const t = useT();
  const [text, setText] = useState("");
  // Phase C — 🎨 image mode. Gated on the selected model's capability:
  // visible for image_generation_via_tools models + Auto, hidden otherwise.
  const [imageMode, setImageMode] = useState(false);
  const models = useModelStore((s) => s.models);
  const activeModel =
    activeModelId && activeModelId !== AUTO_MODEL_ID
      ? models.find((m) => m.id === activeModelId)
      : undefined;
  const canGenerateImages =
    activeModelId === AUTO_MODEL_ID ||
    Boolean(activeModel?.capabilities?.includes("image_generation_via_tools")) ||
    Boolean(activeModel?.capabilities?.includes("image_generation"));
  // Drop image mode when the model can no longer generate images (e.g. the
  // user switched from GPT-4o to a non-tool model).
  useEffect(() => {
    if (!canGenerateImages && imageMode) setImageMode(false);
  }, [canGenerateImages, imageMode]);
  // Sprint 2A — 🎬 video mode (tool path). Gated on the chat model's
  // video_generation_via_tools capability (Auto kept on, like image mode).
  // NOTE: dedicated video models use model_kind="video" (direct-dispatch
  // form) instead — the bare "video_generation" cap is theirs, not chat's.
  const [videoMode, setVideoMode] = useState(false);
  const canGenerateVideos =
    activeModelId === AUTO_MODEL_ID ||
    Boolean(activeModel?.capabilities?.includes("video_generation_via_tools"));
  useEffect(() => {
    if (!canGenerateVideos && videoMode) setVideoMode(false);
  }, [canGenerateVideos, videoMode]);
  // Sprint 2A.5 — Hybrid. A concrete media model (video/audio/tts) swaps
  // the chat composer for a direct-dispatch form. Auto + chat models keep
  // the chat composer (modelKind defaults to "chat" when the model is the
  // Auto sentinel, unknown, or a payload without model_kind).
  const modelKind = activeModel?.model_kind ?? "chat";
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
  // Render bucket: when conversationId is present, follow it; when
  // it's null we read from the DRAFT bucket so files staged before
  // ensureConv resolves still produce chips. Both buckets carry the
  // exact same PendingFile shape so FilePreviewCard doesn't notice
  // the difference.
  const renderBucket = conversationId ?? DRAFT_CONV_KEY;
  const pendingFiles = useChatStore(
    (s) => s.pendingFiles[renderBucket] ?? EMPTY_PENDING_FILES,
  );
  const addPendingFile = useChatStore((s) => s.addPendingFile);
  const updateFileProgress = useChatStore((s) => s.updateFileProgress);
  const markFileReady = useChatStore((s) => s.markFileReady);
  const markFileError = useChatStore((s) => s.markFileError);
  const setFileExtractionStatus = useChatStore((s) => s.setFileExtractionStatus);
  const removePendingFile = useChatStore((s) => s.removePendingFile);
  const clearPendingFiles = useChatStore((s) => s.clearPendingFiles);
  const stageOutgoingFiles = useChatStore((s) => s.stageOutgoingFiles);

  // Auto Thinking Mode — there is no user-facing reasoning control. The
  // system decides whether to think by picking the right model: a complex
  // ``auto`` turn is routed to a reasoning model by the key-aware
  // reasoning router, and the gateway's
  // ``orchestrator._maybe_enable_adaptive_reasoning`` auto-enables thinking
  // whenever the resolved model supports it. The ThinkingBlock in
  // MessageBubble still renders the reasoning output whenever the model
  // produces it — display is preserved, the manual toggle is removed.

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
  // Extraction is a SEPARATE, non-blocking phase: the upload resolves
  // as soon as the bytes land, then the server reads the document in
  // the background (OCR on a scanned PDF can take minutes). We say what
  // is happening — "Reading the document…" — but we deliberately do not
  // gate `canSend` on it. The user can type and send straight away; the
  // send path waits server-side for the text so the model never answers
  // about a document it hasn't been given.
  const isReadingDocument = pendingFiles.some(
    (p) => p.status === "ready" && isExtracting(p.extractionStatus),
  );
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
    // Hand the attachment metadata to the store BEFORE onSend. The
    // clearPendingFiles calls below run synchronously, but on the
    // landing page onSend awaits ensureConv() before it reaches
    // sendMessage — so sendMessage would otherwise read an
    // already-emptied bucket and render the user's own message with no
    // file chips (B1).
    const idSet = new Set(readyFileIds);
    stageOutgoingFiles(
      pendingFiles
        .filter((p) => p.serverId && idSet.has(p.serverId))
        .map((p) => ({
          id: p.serverId!,
          filename: p.file.name,
          content_type: p.file.type || "application/octet-stream",
          file_size_bytes: p.file.size,
          extraction_status: p.extractionStatus ?? "completed",
          created_at: new Date().toISOString(),
        })),
    );
    onSend(t, readyFileIds, imageMode, videoMode);
    setText("");
    // Clear whichever bucket the visible chips were rendering from —
    // covers both the conv-page case (real id) and the landing
    // path where the chips might still be on the DRAFT key at the
    // moment of send (e.g. user attached + sent very quickly before
    // ensureConv resolved).
    clearPendingFiles(renderBucket);
    if (conversationId && conversationId !== renderBucket) {
      clearPendingFiles(conversationId);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  // ── File upload pipeline ─────────────────────────────────
  //
  // Two-phase design so the FilePreviewCard chip appears INSTANTLY
  // when the user picks a file:
  //
  //   Phase 1 (synchronous): validate every File, then stage each
  //     accepted entry into the current render bucket via
  //     addPendingFile. Because that bucket is `conversationId ??
  //     DRAFT_CONV_KEY`, the same bucket the render selector reads,
  //     React schedules an immediate re-render with the new chip.
  //   Phase 2 (async): resolve the real conversation id via
  //     ensureConvId. If it differs from the staging bucket (landing
  //     page with no conversationId yet), migrate the pending rows
  //     onto the real id so the upload completion handlers can find
  //     them. Then run the bounded-parallel upload.
  //
  // Every silent failure path now emits a toast — previously the
  // user could click +, pick a file, and see absolutely nothing if
  // ensureConv silently returned null (e.g. landing page with no
  // selected model). Now they get a clear "Couldn't create
  // conversation" / "Upload failed" surface.

  async function ensureConvId(): Promise<string | null> {
    if (conversationId) return conversationId;
    if (!onRequestConversation) return null;
    try {
      return await onRequestConversation();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("ChatInput.ensureConvId: onRequestConversation threw", err);
      toast(tOutside("chat.toast.prepareFailed"));
      return null;
    }
  }

  // uploadOneStaged splits the convId into two:
  //   - netConvId : the conversation the file is POSTed against on
  //                 the backend (returns a serverId tied to it)
  //   - chipBucket: the chatStore key where the visible chip lives,
  //                 used for progress / ready / error updates
  // Most of the time these are equal; on the landing page the chip
  // bucket is DRAFT_CONV_KEY while the network target is the real
  // conversation eager-created by ensureConv.
  async function uploadOneStaged(
    netConvId: string,
    chipBucket: string,
    pendingId: string,
    file: File,
  ) {
    try {
      const row = await api.uploadFile(netConvId, file, {
        onProgress: (p) => updateFileProgress(chipBucket, pendingId, p),
      });
      markFileReady(chipBucket, pendingId, row.id, row.extraction_status);
      // The upload call now returns before the server has finished
      // reading the document, so follow the row until extraction lands.
      // Fire-and-forget: the chip is already usable and sendable.
      watchExtraction(netConvId, row.id, row.extraction_status, (status) =>
        setFileExtractionStatus(chipBucket, pendingId, status),
      );
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : tOutside("file.err.uploadFailed");
      markFileError(chipBucket, pendingId, msg);
      toast(msg);
    }
  }

  async function handleFiles(filesRaw: File[]) {
    // Per-message cap. Count rows already in the render bucket; we
    // re-read the selector value because closures see the snapshot
    // captured at render time and that's exactly what the user
    // sees right now.
    const existing = pendingFiles.filter((p) => p.status !== "error").length;
    const slots = Math.max(0, MAX_FILES_PER_MESSAGE - existing);
    if (slots === 0) {
      toast(tOutside("input.toast.maxFiles", { max: String(MAX_FILES_PER_MESSAGE) }));
      return;
    }

    const accepted: File[] = [];
    for (const f of filesRaw) {
      if (accepted.length === slots) {
        toast(tOutside("input.toast.slotsLeft", { n: String(slots) }));
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

    // Phase 1 — stage every accepted file IMMEDIATELY at the
    // render bucket. This is what makes the chip pop the instant
    // the file picker closes; no waiting on the network round-trip
    // for createConversation. The CRUCIAL invariant: every
    // subsequent store call for THIS upload batch (progress, ready,
    // error) uses the same staging bucket the chips were rendered
    // into. The upload's NETWORK target (realConvId) is independent
    // of the chip bucket; the backend tags returned serverIds to
    // the real conv, and the submit() path forwards those serverIds
    // through onSend so the message lands correctly.
    const stagingBucket = conversationId ?? DRAFT_CONV_KEY;
    const pendingIds = accepted.map((f) => addPendingFile(stagingBucket, f));

    // Phase 2 — resolve the real conv id. On the conv page this is
    // synchronous (returns props.conversationId); on the landing
    // page this awaits onRequestConversation → createConversation.
    const realConvId = await ensureConvId();
    if (!realConvId) {
      // Conv create failed (or no model selected on landing). Mark
      // every staged row as error so the user sees WHY their chip
      // turned red. The toast inside ensureConvId already surfaced
      // the root cause.
      for (const pid of pendingIds) {
        markFileError(
          stagingBucket,
          pid,
          "Couldn't create conversation for upload",
        );
      }
      return;
    }

    // Bounded parallel upload. Each worker uploads to realConvId
    // (the network target) but updates the staging bucket (where
    // the visible chip lives) for progress + final status. Keeping
    // the chip bucket constant across the entire batch avoids any
    // selector flicker if `conversationId` flips from null to a
    // real id mid-upload (landing → conv page transition).
    let cursor = 0;
    const workers: Promise<void>[] = [];
    for (let i = 0; i < Math.min(MAX_CONCURRENT_UPLOADS, accepted.length); i++) {
      workers.push(
        (async () => {
          while (cursor < accepted.length) {
            const idx = cursor++;
            await uploadOneStaged(
              realConvId,
              stagingBucket,
              pendingIds[idx],
              accepted[idx],
            );
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
  // fires when the composer is focused. The handler stays bound
  // even before a conversation exists — handleFiles → ensureConvId
  // eager-creates one for the landing page.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const el = taRef.current;
    if (!el) return;
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
  }, []);

  // Sprint 2A.5 — direct-dispatch path: a media model renders its gen form
  // in place of the chat composer. The ModelSelector stays visible so the
  // user can switch back to a chat model.
  if (modelKind !== "chat" && activeModel) {
    return (
      <div className="safe-bottom bg-bg-base px-3 py-3 md:px-6 md:py-4">
        <div className="mx-auto flex w-full max-w-chat flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <MediaModeIndicator kind={modelKind} modelName={activeModel.name} />
            <ModelSelector
              activeModelId={activeModelId}
              disabled={streaming}
              compact
              onPick={onModelPick ? (m) => onModelPick(m.id) : undefined}
            />
          </div>
          {modelKind === "video" ? (
            <VideoGenForm model={activeModel} conversationId={conversationId} />
          ) : null}
          {modelKind === "audio" ? (
            <AudioGenForm model={activeModel} mode="music" conversationId={conversationId} />
          ) : null}
          {modelKind === "tts" ? (
            <AudioGenForm model={activeModel} mode="tts" conversationId={conversationId} />
          ) : null}
        </div>
      </div>
    );
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
              than scrolling horizontally — feels native to the pill.
              The bucket is `conversationId ?? DRAFT_CONV_KEY` so a
              landing-page user who picked a file before the conv
              row was created still sees their chip immediately. */}
          {pendingFiles.length > 0 ? (
            <div className="flex flex-wrap gap-2 px-3 pt-3">
              {pendingFiles.map((p) => (
                <FilePreviewCard
                  key={p.id}
                  pending={p}
                  onRemove={() => removePendingFile(renderBucket, p.id)}
                  onRetry={
                    p.status === "error"
                      ? () => {
                          // Retry path: drop the failed row + re-run
                          // the full handleFiles pipeline against the
                          // same File. handleFiles re-resolves the
                          // conv id, restakes the chip, and uploads.
                          removePendingFile(renderBucket, p.id);
                          void handleFiles([p.file]);
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
                  : isReadingDocument
                    ? t("input.placeholder.reading")
                    : isUploading
                      ? t("input.placeholder.uploading")
                      : imageMode
                      ? t("input.placeholder.image")
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
                paperclip per the Claude reference; same behaviour.
                Renders even on the landing page where no
                conversation exists yet — the click handler delegates
                to handleFiles, which eager-creates a conv via
                onRequestConversation before kicking off the upload.
                Hidden only when no upload path is wired (no
                conversation in props AND no provider callback). */}
            <div className="flex items-center">
              {conversationId || onRequestConversation ? (
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
              {canGenerateImages && !streaming ? (
                <button
                  type="button"
                  onClick={() => {
                    setImageMode((v) => !v);
                    setVideoMode(false);
                  }}
                  aria-label={t("input.imageMode")}
                  title={t("input.imageMode")}
                  aria-pressed={imageMode}
                  disabled={disabled}
                  className={`focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    imageMode
                      ? "bg-violet-500/15 text-violet-400"
                      : "text-text-tertiary hover:bg-bg-elevated hover:text-text-primary"
                  }`}
                >
                  <ImagePlus className="h-[18px] w-[18px]" aria-hidden />
                </button>
              ) : null}
              {canGenerateVideos && !streaming ? (
                <button
                  type="button"
                  onClick={() => {
                    setVideoMode((v) => !v);
                    setImageMode(false);
                  }}
                  aria-label={t("input.videoMode")}
                  title={t("input.videoMode")}
                  aria-pressed={videoMode}
                  disabled={disabled}
                  className={`focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    videoMode
                      ? "bg-rose-500/15 text-rose-400"
                      : "text-text-tertiary hover:bg-bg-elevated hover:text-text-primary"
                  }`}
                >
                  <Clapperboard className="h-[18px] w-[18px]" aria-hidden />
                </button>
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

// Re-exported so callers (like the landing page's drag-and-drop
// wrapper) can stage chips into the SAME bucket the ChatInput
// instance is rendering from.
export { DRAFT_CONV_KEY };

// Imperative API for the drag-and-drop wrapper. The conversation
// page's drop handler calls this directly rather than re-implementing
// validation. `chipBucket` controls where the visible PendingFile
// rows live in chatStore — defaults to netConvId, but the landing
// page passes DRAFT_CONV_KEY so the chips render in the same place
// the landing's ChatInput is reading from. `netConvId` is always
// the real conversation id (uploads go there on the backend).
export async function uploadFiles(
  netConvId: string,
  files: File[],
  store: {
    addPendingFile: (id: string, f: File) => string;
    updateFileProgress: (id: string, pid: string, p: number) => void;
    markFileReady: (
      id: string,
      pid: string,
      sid: string,
      extractionStatus?: ExtractionStatus,
    ) => void;
    markFileError: (id: string, pid: string, err: string) => void;
    /** Optional so existing callers that don't care about the
     *  background-extraction phase keep compiling unchanged. */
    setFileExtractionStatus?: (
      id: string,
      pid: string,
      extractionStatus: ExtractionStatus,
    ) => void;
  },
  chipBucket: string = netConvId,
): Promise<void> {
  for (const f of files) {
    const v = validateFile(f);
    if (!v.ok) {
      toast(`${f.name}: ${v.reason}`);
      continue;
    }
    const pid = store.addPendingFile(chipBucket, f);
    try {
      const row = await api.uploadFile(netConvId, f, {
        onProgress: (p) => store.updateFileProgress(chipBucket, pid, p),
      });
      store.markFileReady(chipBucket, pid, row.id, row.extraction_status);
      const onStatus = store.setFileExtractionStatus;
      if (onStatus) {
        watchExtraction(netConvId, row.id, row.extraction_status, (status) =>
          onStatus(chipBucket, pid, status),
        );
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Upload failed.";
      store.markFileError(chipBucket, pid, msg);
      toast(msg);
    }
  }
}
