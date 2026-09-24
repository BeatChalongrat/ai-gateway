"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { useComposerHeightProbe } from "@/hooks/useBottomInset";
import { useDismissable } from "@/hooks/useDismissable";

import { BrainCircuit, Clapperboard, ImagePlus } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { beginUpload, endUpload } from "@/lib/uploadRegistry";
import { useHostedMode } from "@/hooks/useHostedMode";
import { useKnowledgeAvailable } from "@/hooks/useKnowledgeAvailable";
import { loadSkillTemplates, skillSlashCommands } from "@/lib/skillTemplates";
import {
  clearDraft,
  loadDraft,
  migrateDraft,
  NEW_CHAT_DRAFT_KEY,
  saveDraft,
} from "@/lib/draftStore";
import type { ExtractionStatus } from "@/lib/fileExtraction";
import type { FileAttachment } from "@/lib/types";
import { isExtracting, watchExtraction } from "@/lib/fileExtraction";
import {
  admitFilesForMessage,
  FILE_PICKER_ACCEPT,
  FILE_PICKER_ACCEPT_NO_IMAGES,
  isImageFile,
  uploadErrorMessage,
  uploadErrorRetryable,
} from "@/lib/fileUtil";
import { gateImagesForVision, modelAcceptsImages, offerVisionSwitch, visionAlternative } from "@/lib/visionGate";
import { detectImageIntent } from "@/lib/imageIntent";
import { tOutside, useT } from "@/lib/i18n";
import { shouldSendOnEnter } from "@/lib/keyboard";
import {
  applySlashAvailability,
  filterSlashCommands,
  slashQuery,
  type SlashCommand,
  type SlashOffer,
} from "@/lib/slashCommands";
import { toast, toastError } from "@/components/ui/Toast";
import {
  EDIT_MESSAGE_EVENT,
  VOICE_TOGGLE_EVENT,
  lastEditableUserMessageId,
  maxTextareaHeight,
  shouldEditLastOnArrowUp,
} from "@/lib/composerShortcuts";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { useWhisperInput } from "@/hooks/useWhisperInput";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";
import { useVoiceStore } from "@/stores/voiceStore";

import { FilePreviewCard } from "./FilePreviewCard";
import { FolderChip } from "./FolderChip";
import { KBSelector } from "./KBSelector";
import { ModelSelector } from "./ModelSelector";
import { PermissionModeSelector } from "./PermissionModeSelector";
import { RecordingBar } from "./RecordingBar";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { SlashMenu } from "./SlashMenu";
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
  // FIX130 C2 — the ฐานความรู้ button only when a knowledge base can work
  // (or one is already bound here, so it can still be unbound).
  const kbAvailable = useKnowledgeAvailable();
  const hosted = useHostedMode();
  const router = useRouter();
  const [text, setText] = useState("");

  // ── Composer draft persistence (Batch 1 W1 — desktop parity) ────
  // Typed-but-unsent text survives conversation switches and reloads:
  // debounced (~1s) save while typing, immediate flush on switch and
  // unmount, delete on send. Invisible beyond the text reappearing —
  // mirrors ChatViewModel.cs "Composer draft persistence (Phase 3 C4)".
  const draftKey = conversationId ?? NEW_CHAT_DRAFT_KEY;
  // Render-time mirrors so the flush paths (key-change effect, unmount
  // cleanup) read the CURRENT text/key without re-running per keystroke.
  const textRef = useRef(text);
  textRef.current = text;
  const draftKeyRef = useRef(draftKey);

  // Restore on mount + handle an in-place key change. The only expected
  // in-place change is a landing page's eager-create (null → real id):
  // the visible text belongs to the SAME logical conversation, so the
  // entry migrates instead of loading (an empty load would clobber what
  // the user is typing). NOTE: the current /chat landing intentionally
  // pins conversationId=null for its instance's lifetime, so this branch
  // is defensive — landing drafts live under NEW_CHAT_DRAFT_KEY and
  // restore when the landing page is revisited.
  useEffect(() => {
    const prev = draftKeyRef.current;
    if (prev !== draftKey) {
      draftKeyRef.current = draftKey;
      if (prev === NEW_CHAT_DRAFT_KEY && conversationId) {
        migrateDraft(prev, draftKey);
        saveDraft(draftKey, textRef.current);
        return;
      }
      // Any other in-place switch: flush the outgoing conversation's
      // text, then fall through to restore the incoming one.
      saveDraft(prev, textRef.current);
    }
    const restored = loadDraft(draftKey);
    if (restored !== null && restored !== textRef.current) {
      setText(restored);
      // Caret at the end of the restored text. No focus() call — the
      // restore must never steal focus or pop the soft keyboard.
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (!ta) return;
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey, conversationId]);

  // FIX122 Part C — a send that failed before the server took it hands its
  // text back (chatStore.returnedDrafts; lib/sendOutbox has already put it
  // in this conversation's draft, which is what a composer that mounts
  // later restores). Into an EMPTY box only: what the person has typed
  // since is theirs.
  const returnedDraft = useChatStore((s) =>
    conversationId ? s.returnedDrafts[conversationId] : undefined,
  );
  const consumeReturnedDraft = useChatStore((s) => s.consumeReturnedDraft);
  useEffect(() => {
    if (!returnedDraft || !conversationId) return;
    consumeReturnedDraft(conversationId, returnedDraft.id);
    if (textRef.current.trim()) return;
    setText(returnedDraft.text);
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta) return;
      const end = ta.value.length;
      ta.setSelectionRange(end, end);
    });
  }, [returnedDraft, conversationId, consumeReturnedDraft]);

  // Debounced (~1s) save while typing; the effect cleanup doubles as
  // the per-keystroke debounce reset. saveDraft with empty text deletes
  // the entry, so clearing the composer clears the draft too.
  useEffect(() => {
    const key = draftKey;
    const id = window.setTimeout(() => saveDraft(key, text), 1000);
    return () => window.clearTimeout(id);
  }, [text, draftKey]);

  // Flush on unmount so a fast click-away (before the debounce fires)
  // still lands the draft — and on pagehide, because a hard reload or
  // tab close destroys the document WITHOUT running React cleanups,
  // which would lose anything typed inside the debounce window.
  // Mount-only: the refs carry current values.
  useEffect(() => {
    const flush = () => saveDraft(draftKeyRef.current, textRef.current);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  // Phase 10 — slash quick actions. The menu is derived from `text`
  // (single source of truth): no separate "is open" flag to fall out of
  // sync with what the user actually typed.
  const slashToken = slashQuery(text);
  // FIX33 B6 — the user's enabled skills join the "/" list as templates.
  // Loaded once per mount (and per hosted/web switch); a failure = no
  // skill entries, never a broken menu.
  const [skillCommands, setSkillCommands] = useState<SlashCommand[]>([]);
  useEffect(() => {
    let alive = true;
    void loadSkillTemplates(hosted).then((templates) => {
      if (alive) setSkillCommands(skillSlashCommands(templates));
    });
    return () => {
      alive = false;
    };
  }, [hosted]);
  // Reset the highlight whenever the candidate list changes, so Enter
  // never runs the action that happened to sit at a stale index.
  useEffect(() => {
    setSlashIndex(0);
  }, [slashToken]);
  // Phase C — 🎨 image mode. Gated on the selected model's capability:
  // visible for image_generation_via_tools models + Auto, hidden otherwise.
  const [imageMode, setImageMode] = useState(false);
  // FIX62 — an image request typed at a model that cannot make images is
  // held here (the text the user sent) so the composer can say why and
  // let the same text through on the next send, as a plain question.
  const [imageIntentHeld, setImageIntentHeld] = useState<string | null>(null);
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
  // user switched from GPT-4o to a non-tool model). FIX47.6 Part C — the
  // drop is never silent: the toast says what happened and why at the
  // moment of the switch, instead of the mode just vanishing.
  useEffect(() => {
    if (!canGenerateImages && imageMode) {
      setImageMode(false);
      toast(tOutside("input.imageMode.droppedForModel", {
        model: activeModel?.name ?? activeModelId ?? "",
      }));
    }
    // activeModel/-Id are display inputs for the toast only — the mode
    // drop must key off the capability flip alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canGenerateImages, imageMode]);
  // Sprint 2A — 🎬 video mode (tool path). Gated on the chat model's
  // video_generation_via_tools capability (Auto kept on, like image mode).
  // NOTE: dedicated video models use model_kind="video" (direct-dispatch
  // form) instead — the bare "video_generation" cap is theirs, not chat's.
  const [videoMode, setVideoMode] = useState(false);
  const canGenerateVideos =
    activeModelId === AUTO_MODEL_ID ||
    Boolean(activeModel?.capabilities?.includes("video_generation_via_tools"));
  // FIX47.6 Part C — the video button renders (disabled, with the
  // reason) when the FEATURE exists in this deployment but the selected
  // model can't drive it. When no model in the catalog carries the
  // capability the feature is off and no button renders at all — the
  // FIX11/FIX34.1 rule (no affordance for something nothing can do).
  const videoFeatureAvailable = models.some((m) =>
    m.capabilities?.includes("video_generation_via_tools"),
  );
  useEffect(() => {
    if (!canGenerateVideos && videoMode) {
      setVideoMode(false);
      toast(tOutside("input.videoMode.droppedForModel", {
        model: activeModel?.name ?? activeModelId ?? "",
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canGenerateVideos, videoMode]);
  // Sprint 2A.5 — Hybrid. A concrete media model (video/audio/tts) swaps
  // the chat composer for a direct-dispatch form. Auto + chat models keep
  // the chat composer (modelKind defaults to "chat" when the model is the
  // Auto sentinel, unknown, or a payload without model_kind).
  const modelKind = activeModel?.model_kind ?? "chat";

  // FIX84 row 2 — the menu offers exactly what the toolbar offers: the
  // same capability answers decide usable / disabled-with-reason / hidden
  // (lib/slashCommands.applySlashAvailability). No second list.
  const imageFeatureAvailable = models.some(
    (m) =>
      m.capabilities?.includes("image_generation_via_tools") ||
      m.capabilities?.includes("image_generation"),
  );
  const slashCommands =
    slashToken === null
      ? []
      : applySlashAvailability(filterSlashCommands(slashToken, skillCommands), {
          canGenerateImages,
          imageFeatureAvailable,
          canGenerateVideos,
          videoFeatureAvailable,
          hosted,
        });
  const slashOpen = slashToken !== null;
  const [slashIndex, setSlashIndex] = useState(0);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // W3 — vision gating. The picker's accept-list and the send gate
  // react to the model instantly; the paste/drop paths gate through
  // gateImagesForVision (reading the CURRENT id via the ref, because
  // the paste listener binds once at mount and would otherwise close
  // over a stale model).
  const acceptsImages = modelAcceptsImages(activeModelId ?? null, models);
  const visionAlt = acceptsImages ? null : visionAlternative(activeModelId ?? null, models);
  const activeModelIdRef = useRef(activeModelId);
  activeModelIdRef.current = activeModelId;
  // Phase 11 — Thinking Mode toggle. The store action + server columns have
  // existed since T4; nothing ever called them, so a reasoning-capable model
  // could never actually be told to think. FIX51 — capability is told at
  // SELECTION time (FIX47.6 Part C rule): a concrete model that can't
  // reason shows the toggle disabled with the reason, and the
  // after-the-fact "answered without extended thinking" line under the
  // reply is gone. Auto stays buttonless: the router may land on either
  // kind, so the toggle has nothing truthful to say there.
  const canReason = Boolean(activeModel?.reasoning?.supported);
  // FIX125 M3 — the server lists "off" in reasoning.modes ONLY for a model
  // that can really stop thinking. A reasoning model without it thinks on
  // every turn whatever the conversation's flag says, so the toggle must not
  // offer an OFF state: it shows on, disabled, and says why.
  const reasoningModes = activeModel?.reasoning?.modes;
  const alwaysThinks =
    canReason && !(Array.isArray(reasoningModes) && reasoningModes.includes("off"));
  const canToggleReasoning = canReason && !alwaysThinks;
  const conversation = useChatStore((s) =>
    conversationId ? s.conversations.find((c) => c.id === conversationId) : undefined,
  );
  const reasoningOn = alwaysThinks || Boolean(conversation?.reasoning_enabled);
  const setConversationReasoning = useChatStore((s) => s.setConversationReasoning);
  // FIX36 Part B — the per-conversation cost ceiling stays server-side
  // (conversation.routing_tier + PATCH endpoint), but the composer no longer
  // exposes it: the user only sees Auto vs an explicit model, and the
  // workspace default ceiling lives in admin settings. Tier = a ceiling,
  // never a forced band — Auto picks the cheapest adequate model itself.
  const isAutoModel = activeModelId === AUTO_MODEL_ID || !activeModelId;
  const [isMobile, setIsMobile] = useState(false);
  // Distance (px) the on-screen keyboard is occluding the bottom of
  // the layout viewport. Updated from visualViewport.resize so the
  // composer floats just above the keyboard on iOS / Android.
  const [keyboardInset, setKeyboardInset] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // FIX86 — the ONE producer of the vertical axis: whatever this box
  // measures, every bottom-floating element sits above it.
  const composerRootRef = useRef<HTMLDivElement>(null);
  // FIX89 Part B — the "+" is a MENU on the desktop, where connectors exist:
  // attach files, or open the connectors page (Claude reaches its connector
  // directory from the composer the same way). In a plain browser there is no
  // connectors page at all — `/chat/connectors` bounces back to `/chat`
  // (useDesktopOnlyRoute) — so there the "+" stays what it was: one click,
  // straight to the file picker.
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  useComposerHeightProbe(composerRootRef);
  // Explicit IME composition tracking (Phase 4, Thai IME). WebView2's native
  // KeyboardEvent.isComposing can be unreliable in an embedded control, so we
  // also track composition via compositionstart/end and OR it into the
  // Enter-to-send guard. This guarantees Enter during composition commits the
  // candidate rather than submitting a half-formed word.
  const composingRef = useRef(false);

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
  // FIX89 Part A — the cap is a relationship, not two magic numbers: about
  // half of the window the composer is actually in, minus the composer's own
  // chrome, floored at what the cap used to be so it can only grow. Measured
  // here (not assumed) and re-measured whenever the window or the chrome
  // changes; `visualViewport` is what shrinks when a soft keyboard opens.
  const [viewportHeight, setViewportHeight] = useState(0);
  const [chromeHeight, setChromeHeight] = useState(0);
  const maxTextareaHeightPx = maxTextareaHeight(viewportHeight, chromeHeight, isMobile);
  const closeAttachMenu = useCallback(() => setAttachMenuOpen(false), []);
  useDismissable(attachMenuRef, attachMenuOpen, closeAttachMenu);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const sync = () => setViewportHeight(vv?.height ?? window.innerHeight);
    sync();
    window.addEventListener("resize", sync);
    vv?.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("resize", sync);
      vv?.removeEventListener("resize", sync);
    };
  }, []);

  // The chrome around the text area (toolbar row, padding, chips): the box's
  // height minus the text area's. A ResizeObserver keeps it live, so a second
  // row of attachment chips takes its room out of the text area's share
  // rather than pushing the box past half the window.
  useEffect(() => {
    const root = composerRootRef.current;
    const ta = taRef.current;
    if (!root || !ta || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const next = Math.max(0, Math.round(root.getBoundingClientRect().height - ta.getBoundingClientRect().height));
      setChromeHeight((prev) => (Math.abs(prev - next) > 1 ? next : prev));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    ro.observe(ta);
    return () => ro.disconnect();
  }, []);

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
  const enqueueMessage = useChatStore((s) => s.enqueueMessage);
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
    ta.style.height = `${Math.min(ta.scrollHeight, maxTextareaHeightPx)}px`;
  }, [text, maxTextareaHeightPx]);

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

  /** FIX88 Part B — focus the composer after a send unless the device is
   *  touch or the composer already has it. Reads activeElement after one
   *  frame so a click on Send has finished committing its state swap. */
  function refocusAfterSend() {
    if (typeof window === "undefined") return;
    if (!window.matchMedia("(pointer: fine)").matches) return;
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta || document.activeElement === ta) return;
      ta.focus({ preventScroll: true });
    });
  }

  // When a stream ends (completed / stopped / errored / regenerate
  // finished), give the caret back to the composer so the user can keep
  // typing without a click — but ONLY when focus was actually lost:
  // activeElement is body/null or a node no longer in the document (an
  // unmounted button). Focus the user deliberately moved into another
  // field, dialog, or control stays where they put it, and the composer
  // itself is never re-focused (that would scroll / reset the caret).
  // Touch devices are excluded so the soft keyboard doesn't pop over the
  // finished reply — same (pointer: fine) guard as the model picker's
  // search autofocus. Fires on the streaming true→false edge only, never
  // on mount.
  const wasStreamingRef = useRef(streaming);
  useEffect(() => {
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = streaming;
    if (!wasStreaming || streaming) return;
    if (typeof window === "undefined") return;
    if (!window.matchMedia("(pointer: fine)").matches) return;
    // One frame so the stop→send state swap has committed before we
    // inspect activeElement (no arbitrary timeout).
    const raf = requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta) return;
      const active = document.activeElement;
      if (active === ta) return;
      const focusLost =
        active === null ||
        active === document.body ||
        !document.contains(active);
      if (!focusLost) return;
      // FIX127 D3 — a person holding a text selection in the answer has
      // focus on <body> too; taking it would collapse their selection.
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed) return;
      ta.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(raf);
  }, [streaming]);

  // FIX127 D4 — the composer has focus when a chat opens on a device with a
  // pointer (the desktop app, a laptop): typing can start at once. Phones
  // are left alone (focusing pops the keyboard). Focus placed anywhere
  // else by the person — or by a dialog — is never taken; only <body> and
  // the layout's route-change landing on <main> count as "nobody's".
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia("(pointer: fine)").matches) return;
    const raf = requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta || disabled) return;
      const active = document.activeElement;
      const nobody = active === null || active === document.body || (active instanceof HTMLElement && active.tagName === "MAIN");
      if (!nobody) return;
      ta.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

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
  // FIX130 C3.4 — a message is never sent with an attachment silently
  // missing. A failed chip used to be skipped by the send and then swept
  // away with the rest (NIGHT2 F6: the file never reached the model and the
  // answer said "รับข้อความได้ครบ"). Now any failed chip holds the send, and
  // the composer says which file and what to do (retry or remove).
  const failedChips = pendingFiles.filter((p) => p.status === "error");
  // FIX130 C3.4 — the image the selected model cannot read. This was the
  // real "5 chips incl. one error → Send does nothing" (NIGHT2 F5): the run
  // had an image at deepseek-v4-flash (no vision), and the FIX27 gate held
  // the send with a toast that was gone within seconds. It now also says so
  // in the composer for as long as it holds.
  const visionBlocked = !acceptsImages && pendingFiles.some((p) => p.status !== "error" && isImageFile(p.file));
  const canSend =
    !disabled &&
    !streaming &&
    !isUploading &&
    failedChips.length === 0 &&
    (text.trim().length > 0 || readyFileIds.length > 0);

  function submit() {
    // FIX127 D1 — Enter while an answer streams used to do nothing. The
    // message now waits in the queue above the composer (editable,
    // removable, "send now"), and is sent as its own turn when the answer
    // finishes. Ready attachments travel with it; one still uploading
    // stays in the composer for the next message.
    if (streaming && !disabled && conversationId && failedChips.length === 0 && (text.trim().length > 0 || readyFileIds.length > 0)) {
      enqueueMessage(conversationId, text.trim(), readyFileIds);
      setText("");
      textRef.current = "";
      clearDraft(draftKey);
      for (const p of pendingFiles) {
        if (p.status === "ready" && p.serverId) removePendingFile(renderBucket, p.id, { keepFile: true });
      }
      refocusAfterSend();
      return;
    }
    if (!canSend) return;
    // W3 — a user who attached an image and THEN switched to a
    // non-vision model keeps the attachment; the send is blocked with
    // the fix named instead of silently dropping their file or letting
    // the provider fail opaquely.
    if (
      !acceptsImages &&
      pendingFiles.some((p) => p.status !== "error" && isImageFile(p.file))
    ) {
      // FIX27 §2.2: the same one-click switch offer as attach time.
      offerVisionSwitch(activeModelId ?? null, models);
      return;
    }
    const t2 = text.trim();
    // FIX62 — an explicit image request at a model that cannot make
    // images is not sent: it would either fail at the provider or come
    // back as prose pretending to be a picture. The composer keeps the
    // text, names the way out, and lets the SAME text through on the
    // next send (the user has then chosen to ask in words). Shared by
    // both clients — the desktop opens this page — so the desktop, which
    // never passes through chat-edge's intent check, gets the same
    // answer as the web.
    if (!canGenerateImages && detectImageIntent(t2) && imageIntentHeld !== t2) {
      setImageIntentHeld(t2);
      return;
    }
    setImageIntentHeld(null);
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
          unreadable_reason: p.unreadableReason,
          created_at: new Date().toISOString(),
        })),
    );
    onSend(t2, readyFileIds, imageMode, videoMode);
    setText("");
    // FIX122 Part C — and the mirror, NOW. In an empty conversation the send
    // swaps the page's layout (centred hero → thread), which unmounts this
    // composer in the very commit that would have rendered it empty: the
    // render that refreshes textRef never happens, and the unmount flush
    // below wrote the just-sent text back as a draft — after clearDraft —
    // for the next composer to restore. The box came back full, and Enter
    // asked the same thing again.
    textRef.current = "";
    // FIX88 Part B — the caret comes back to the composer after a send,
    // however the send was fired. A mouse click on Send leaves focus on
    // that button (which then becomes Stop), and the stream-end restore
    // below deliberately leaves focus that sits on a live control alone —
    // so nothing ever gave the composer back, and the next message needed
    // a second click. The send button is not "somewhere the user moved
    // focus to": it is the control that just fired. ChatGPT and Claude
    // both keep the composer focused after a send; so do we. Same touch
    // guard as the restore below (no soft keyboard over the reply), and
    // an already-focused composer is left untouched (no caret reset).
    refocusAfterSend();
    // Delete the persisted draft NOW — the debounced empty-save would
    // also clear it, but a reload inside that 1s window must not
    // resurrect an already-sent message.
    clearDraft(draftKey);
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

  // Every slash action is a shortcut into a flow that already exists —
  // see lib/slashCommands.ts. Running one always clears the token so the
  // composer is left empty and ready for the real message.
  function runSlashCommand(cmd: SlashOffer) {
    // FIX84 row 2 — a listed-but-disabled command says why instead of
    // silently swallowing the token: the reason is toasted and the
    // composer keeps whatever the user typed.
    if (cmd.disabledReasonKey) {
      toast(tOutside(cmd.disabledReasonKey));
      taRef.current?.focus();
      return;
    }
    setText("");
    switch (cmd.id) {
      case "image":
        // Only offered when the selected model can generate images (the
        // availability rule above mirrors the toolbar button).
        setImageMode(true);
        break;
      case "video":
        setVideoMode(true);
        break;
      case "new":
        // Same handler the sidebar button and Cmd+Shift+N use.
        useChatStore.getState().setActive(null);
        window.location.assign("/chat");
        break;
      case "shortcuts":
        setShortcutsOpen(true);
        break;
      case "skill":
        // FIX33 B6 — the template's trigger phrase becomes the draft; the
        // user finishes the sentence and sends, activating the skill.
        setText(cmd.insertText ?? "");
        break;
    }
    taRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Slash menu owns the arrow keys + Enter while it is open, so the
    // user can drive it without leaving the keyboard. Esc closes it by
    // clearing the token (the menu is derived from the text).
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((i) => (slashCommands.length ? (i + 1) % slashCommands.length : 0));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((i) =>
          slashCommands.length ? (i - 1 + slashCommands.length) % slashCommands.length : 0,
        );
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setText("");
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && slashCommands.length > 0) {
        const native = e.nativeEvent as KeyboardEvent;
        // Still respect the IME guard (Phase 7): during composition
        // Enter commits a candidate and must not run a command.
        if (e.key === "Enter" && (native.isComposing || native.keyCode === 229 || composingRef.current)) return;
        e.preventDefault();
        runSlashCommand(slashCommands[Math.min(slashIndex, slashCommands.length - 1)]);
        return;
      }
    }

    // FIX34 Part D — ↑ in an EMPTY composer edits the last message (Claude's
    // binding). With text present the arrow keeps moving the caret. The
    // bubble that owns that message opens its edit box on the event.
    if (
      shouldEditLastOnArrowUp({
        key: e.key,
        text,
        slashOpen,
        hasPendingFiles: pendingFiles.length > 0,
        shiftKey: e.shiftKey,
        altKey: e.altKey,
        ctrlKey: e.ctrlKey,
        metaKey: e.metaKey,
      })
    ) {
      const state = useChatStore.getState();
      const convKey = conversationId ?? state.activeConversationId;
      const messageId = convKey ? lastEditableUserMessageId(state.messages[convKey]) : null;
      if (messageId) {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent(EDIT_MESSAGE_EVENT, { detail: { messageId } }));
        return;
      }
    }

    // Phase 7 — the IME composition guard lives in shouldSendOnEnter:
    // during composition Enter commits the candidate and must NOT send.
    const native = e.nativeEvent as KeyboardEvent;
    if (
      shouldSendOnEnter({
        key: e.key,
        shiftKey: e.shiftKey,
        // OR in the explicitly-tracked composition state (WebView2 robustness).
        isComposing: native.isComposing || composingRef.current,
        keyCode: native.keyCode,
      })
    ) {
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
    // FIX127 A1 — a chip removed while it waited its turn is skipped; one
    // removed mid-transfer is aborted; the id travels as client_id so the
    // server row can be taken back even before the response is seen.
    const ctrl = beginUpload(pendingId, netConvId);
    if (!ctrl) return;
    try {
      const row = await api.uploadFile(netConvId, file, {
        signal: ctrl.signal,
        clientId: pendingId,
        onProgress: (p) => updateFileProgress(chipBucket, pendingId, p),
      });
      endUpload(pendingId);
      markFileReady(chipBucket, pendingId, row.id, row.extraction_status);
      // The upload call now returns before the server has finished
      // reading the document, so follow the row until extraction lands.
      // Fire-and-forget: the chip is already usable and sendable.
      watchExtraction(netConvId, row.id, row.extraction_status, (status, polled) =>
        setFileExtractionStatus(chipBucket, pendingId, status, polled?.unreadable_reason),
      );
    } catch (err) {
      endUpload(pendingId);
      // The user removed the chip: nothing to report, the chip is gone.
      if (err instanceof ApiError && err.code === "ABORTED") return;
      // FIX126 F5 — the chip speaks the UI's language, not the transport's.
      const msg = uploadErrorMessage(err);
      markFileError(chipBucket, pendingId, msg, uploadErrorRetryable(err));
      toastError(msg);
    }
  }

  async function handleFiles(filesInput: File[]) {
    // W3 — vision gate first: images are filtered (with a toast naming
    // a capable model) when the active model can't read them. Reads
    // the CURRENT model id via the ref — this function is also reached
    // from the mount-time paste listener whose closure is stale.
    const filesRaw = gateImagesForVision(
      filesInput,
      activeModelIdRef.current ?? null,
    );
    if (filesRaw.length === 0) return;
    // FIX126 F1 — the shared per-message gate. The live chip count is read
    // from the STORE at this instant, not from the render closure: the
    // paste listener is bound once and used to see the mount-time count.
    const stagingBucket = conversationId ?? DRAFT_CONV_KEY;
    // FIX132 F18 — a refused file is an error: it stays until read.
    const { accepted } = admitFilesForMessage(
      filesRaw,
      liveChipCount(stagingBucket),
      undefined,
      toastError,
    );
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
    const pendingIds = accepted.map((f) => addPendingFile(stagingBucket, f));
    // FIX34 Part D: the paste-as-file undo needs the chip id of what it staged.
    lastStagedRef.current = { bucket: stagingBucket, ids: pendingIds };

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
        markFileError(stagingBucket, pid, tOutside("file.err.noConversation"));
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
  //
  // FIX89 Part A — a long TEXT paste is left alone: it lands in the composer
  // as text, the box grows to about half the window and then scrolls inside
  // itself, and the sent message collapses under the Show more / Show less
  // that MessageBubble already applies over 12 rendered lines. FIX34 turned a
  // paste of 5,000+ characters into a .txt attachment (ChatGPT's rule); the
  // Claude chat app, which is what the owner compared against, does not. There
  // is nothing to undo because nothing happens to the text.
  const lastStagedRef = useRef<{ bucket: string; ids: string[] } | null>(null);
  // The listener below binds once; it calls the CURRENT handleFiles so a
  // paste sees this render's conversation id (FIX126 F1).
  const handleFilesRef = useRef(handleFiles);
  handleFilesRef.current = handleFiles;
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
      if (images.length > 0) {
        e.preventDefault();
        void handleFilesRef.current(images);
      }
      // Text of any length: the browser's own paste, untouched.
    };
    el.addEventListener("paste", onPaste);
    return () => el.removeEventListener("paste", onPaste);
  }, []);

  // FIX34 Part D — Ctrl+Shift+V (global shortcut) toggles the recorder. The
  // hook dispatches an event because the recorder lives here.
  const voiceRef = useRef({ start: startVoice, stop: stopVoice, supported: voiceSupported, recording: isRecording, streaming });
  voiceRef.current = { start: startVoice, stop: stopVoice, supported: voiceSupported, recording: isRecording, streaming };
  useEffect(() => {
    const onToggle = () => {
      const v = voiceRef.current;
      if (!v.supported) {
        toast(tOutside("input.voice.unsupported"));
        return;
      }
      if (v.recording) v.stop();
      else if (!v.streaming) v.start();
    };
    window.addEventListener(VOICE_TOGGLE_EVENT, onToggle);
    return () => window.removeEventListener(VOICE_TOGGLE_EVENT, onToggle);
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
      ref={composerRootRef}
      data-testid="composer-root"
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
          className="relative flex flex-col border border-border bg-bg-surface transition-colors focus-within:border-border-strong"
          style={{
            borderRadius: 24,
            boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)",
            display: isRecording || whisper.isTranscribing ? "none" : undefined,
          }}
        >
          {/* Phase 10 — slash quick actions, floating above the pill. */}
          {slashOpen ? (
            <SlashMenu
              commands={slashCommands}
              activeIndex={slashIndex}
              onPick={runSlashCommand}
            />
          ) : null}

          <input
            ref={fileInputRef}
            type="file"
            // W3 — non-vision models get the image-free accept list so
            // the OS dialog steers away from images; gateImagesForVision
            // in handleFiles stays the real gate ("All files" bypasses
            // accept).
            accept={acceptsImages ? FILE_PICKER_ACCEPT : FILE_PICKER_ACCEPT_NO_IMAGES}
            multiple
            className="hidden"
            onChange={onPickFiles}
          />

          {/* Row 0: pending-file chips. The bucket is
              `conversationId ?? DRAFT_CONV_KEY` so a landing-page user
              who picked a file before the conv row was created still
              sees their chip immediately.
              FIX132 D3 — the row never grows without bound. On a phone
              the 160-px cards stacked one per row: five of them made the
              composer ~830 px tall in a 740-px window, the text box and
              Send went below the screen and the credits pill landed on
              the header. Below sm it is one row that scrolls sideways
              (like ChatGPT/Claude); from sm up it wraps but stops at a
              cap and scrolls inside. */}
          {pendingFiles.length > 0 ? (
            <div
              role="group"
              aria-label={t("input.chipRow.aria")}
              data-testid="composer-chip-row"
              className="flex flex-nowrap gap-2 overflow-x-auto px-3 pt-3 sm:max-h-[248px] sm:flex-wrap sm:overflow-y-auto sm:overflow-x-hidden"
            >
              {pendingFiles.map((p) => (
                <FilePreviewCard
                  key={p.id}
                  pending={p}
                  onRemove={() => removePendingFile(renderBucket, p.id)}
                  onRetry={
                    p.status === "error" && p.retryable !== false
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

          {/* FIX130 C3.4 — why Send is held, for as long as it is held:
              a failed upload (named, with what to do) or an image the
              selected model cannot read. */}
          {failedChips.length > 0 ? (
            <div
              role="alert"
              data-testid="send-blocked-files"
              className="mx-4 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-text-primary"
            >
              <span className="min-w-0 flex-1">
                {failedChips.length === 1
                  ? t(failedChips[0].retryable === false ? "input.sendBlocked.failedOneNoRetry" : "input.sendBlocked.failedOne", { name: failedChips[0].file.name })
                  : t("input.sendBlocked.failedMany", { n: String(failedChips.length) })}
              </span>
              <button
                type="button"
                data-testid="remove-failed-files"
                onClick={() => {
                  for (const p of failedChips) removePendingFile(renderBucket, p.id);
                }}
                className="focus-ring rounded-md border border-border bg-bg-surface px-2 py-1 text-xs font-medium text-text-primary hover:bg-bg-elevated"
              >
                {t("input.sendBlocked.removeFailed")}
              </button>
            </div>
          ) : visionBlocked ? (
            <div
              role="status"
              data-testid="send-blocked-vision"
              className="mx-4 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-bg-elevated px-3 py-2 text-xs text-text-secondary"
            >
              <span className="min-w-0 flex-1">{t("input.sendBlocked.vision", { model: activeModel?.name ?? activeModelId ?? "" })}</span>
              {visionAlt ? (
                <button
                  type="button"
                  onClick={() => useModelStore.getState().selectModel(visionAlt.id)}
                  className="focus-ring rounded-md border border-border bg-bg-surface px-2 py-1 text-xs font-medium text-text-primary hover:border-border-strong"
                >
                  {t("input.visionSwitchTo", { model: visionAlt.name ?? visionAlt.id })}
                </button>
              ) : null}
              {/* FIX132 N6 — the other way out the notice names, one click:
                  the image chips go, the text and other files stay. */}
              <button
                type="button"
                onClick={() => {
                  for (const p of pendingFiles) if (p.status !== "error" && isImageFile(p.file)) removePendingFile(renderBucket, p.id);
                }}
                className="focus-ring rounded-md border border-border bg-bg-surface px-2 py-1 text-xs font-medium text-text-primary hover:border-border-strong"
              >
                {t("input.sendBlocked.removeImages")}
              </button>
            </div>
          ) : null}

          {/* FIX62 — the held image request: why it was not sent, and the
              way out. Cleared on the next send, on a model switch that can
              make images, or by the user editing the text. */}
          {imageIntentHeld !== null && !canGenerateImages ? (
            <div
              role="status"
              data-testid="image-intent-blocked"
              className="mx-4 mt-3 rounded-lg border border-border bg-bg-elevated px-3 py-2 text-xs text-text-secondary"
            >
              {t("input.imageIntent.blocked", { model: activeModel?.name ?? activeModelId ?? "" })}
            </div>
          ) : null}

          {/* Row 1: textarea, full width */}
          <div className="px-4 pt-3">
            <textarea
              ref={taRef}
              data-shortcut="composer"
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
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              className="w-full resize-none bg-transparent text-[15px] leading-relaxed text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-60"
              style={{
                maxHeight: maxTextareaHeightPx,
                transition: "height 100ms ease-out",
              }}
            />
          </div>

          {/* Row 2: attach · folders · permission mode (left) · model + send (right).
              Left is what you bring INTO the turn and under what rules (content,
              folders, permission mode); right is how it goes OUT (image/video
              mode, model, voice, send). FIX25b moved the two desktop controls
              left for that reason — keep constraints-on-the-turn here. */}
          <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
            {/* Left — attach button. The plus mark replaces the
                paperclip per the Claude reference; same behaviour.
                Renders even on the landing page where no
                conversation exists yet — the click handler delegates
                to handleFiles, which eager-creates a conv via
                onRequestConversation before kicking off the upload.
                Hidden only when no upload path is wired (no
                conversation in props AND no provider callback). */}
            <div className="flex min-w-0 items-center gap-1.5">
              {conversationId || onRequestConversation ? (
                <div className="relative" ref={attachMenuRef}>
                  <button
                    type="button"
                    onClick={() => (hosted ? setAttachMenuOpen((v) => !v) : fileInputRef.current?.click())}
                    aria-label={hosted ? t("input.attachMenu.aria") : acceptsImages ? t("input.attach") : t("input.attachNoImages")}
                    title={hosted ? t("input.attachMenu.aria") : acceptsImages ? t("input.attach") : t("input.attachNoImages")}
                    aria-haspopup={hosted ? "menu" : undefined}
                    aria-expanded={hosted ? attachMenuOpen : undefined}
                    disabled={streaming || disabled}
                    data-testid="attach-button"
                    className="focus-ring tap-44 inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
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
                  {hosted && attachMenuOpen ? (
                    <div
                      role="menu"
                      aria-label={t("input.attachMenu.aria")}
                      data-testid="attach-menu"
                      className="popover-appear absolute bottom-full left-0 z-20 mb-2 w-72 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-lg"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        data-testid="attach-menu-files"
                        className="block w-full px-4 py-3 text-left text-sm text-text-primary hover:bg-bg-base"
                        onClick={() => {
                          setAttachMenuOpen(false);
                          fileInputRef.current?.click();
                        }}
                      >
                        <span className="block font-medium">{t("input.attachMenu.files")}</span>
                        <span className="block text-xs text-text-muted">{t("input.attachMenu.filesHint")}</span>
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        data-testid="attach-menu-connectors"
                        className="block w-full border-t border-border px-4 py-3 text-left text-sm text-text-primary hover:bg-bg-base"
                        onClick={() => {
                          setAttachMenuOpen(false);
                          router.push("/chat/connectors");
                        }}
                      >
                        <span className="block font-medium">{t("input.attachMenu.connectors")}</span>
                        <span className="block text-xs text-text-muted">{t("input.attachMenu.connectorsHint")}</span>
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {/* FIX25/25b — desktop only: the granted folders and the permission
                  mode (both render nothing in a plain browser). Control-plane
                  ops only; the model never sees them. */}
              {hosted ? (
                <>
                  <FolderChip disabled={disabled} />
                  <PermissionModeSelector disabled={disabled} />
                </>
              ) : null}
            </div>

            {/* Right — KB selector + model selector pill + send/stop.
                FIX128 D4 — min-w-0 lets this group give way on a 360 px
                screen (the model label truncates) instead of pushing the
                send button past the composer's clipped edge. */}
            <div className="flex min-w-0 items-center gap-1.5">
              {onKBPick && (kbAvailable || activeKBId) ? (
                <KBSelector
                  conversationId={conversationId}
                  activeKBId={activeKBId ?? null}
                  onPick={onKBPick}
                  disabled={streaming}
                />
              ) : null}
              {/* FIX65 — a model that cannot make images has no 🎨 at all.
                  FIX47.6 kept the button visible-but-disabled so a model
                  did not look broken; that worry belonged to a picker
                  that offered every model. Vendor models in the picker
                  now pass a floor (vision + tools + streaming) and the
                  operator's own models sit under a group that says what
                  each one can do, so an absent button is the honest
                  shape — the same rule as 🎬 below, on both clients (the
                  desktop renders this page). Typing an image request at
                  such a model still gets the FIX62 held notice, never a
                  silent no-op. */}
              {!streaming && canGenerateImages ? (
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
                  data-testid="image-mode-button"
                  className={`focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    imageMode
                      ? "bg-violet-500/15 text-violet-400"
                      : "text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                  }`}
                >
                  <ImagePlus className="h-[18px] w-[18px]" aria-hidden />
                </button>
              ) : null}
              {!isAutoModel && activeModel && conversationId && !streaming ? (
                <button
                  type="button"
                  onClick={() => {
                    if (!canToggleReasoning) return;
                    void setConversationReasoning(conversationId, {
                      reasoning_enabled: !reasoningOn,
                    });
                  }}
                  aria-label={
                    alwaysThinks
                      ? t("input.reasoning.alwaysOn")
                      : canReason
                        ? t("input.reasoning")
                        : t("input.reasoning.unsupported")
                  }
                  title={
                    alwaysThinks
                      ? t("input.reasoning.alwaysOn")
                      : canReason
                        ? t(reasoningOn ? "input.reasoning.on" : "input.reasoning.off")
                        : t("input.reasoning.unsupported")
                  }
                  aria-pressed={reasoningOn}
                  aria-disabled={!canToggleReasoning || undefined}
                  disabled={disabled || !canToggleReasoning}
                  data-testid="reasoning-toggle"
                  className={`focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    reasoningOn
                      ? "bg-blue-500/15 text-blue-400"
                      : "text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                  }`}
                >
                  <BrainCircuit className="h-[18px] w-[18px]" aria-hidden />
                </button>
              ) : null}
              {/* FIX9 Part B: video gen isn't wired in the desktop app. FIX34.1
                  Part F: so the desktop gets NO button, not a disabled one with
                  a "not available here yet" tooltip — same rule as the web.
                  FIX65 — and the same rule now covers the MODEL: a model
                  that cannot drive the video tool has no button either,
                  like 🎨 above (one rule, both clients). */}
              {videoFeatureAvailable && canGenerateVideos && !streaming && !hosted ? (
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
                  data-testid="video-mode-button"
                  className={`focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    videoMode
                      ? "bg-rose-500/15 text-rose-400"
                      : "text-text-muted hover:bg-bg-elevated hover:text-text-primary"
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
                needsVision={pendingFiles.some((p) => p.status !== "error" && isImageFile(p.file))}
              />
              {voiceSupported && !streaming ? (
                <button
                  type="button"
                  onClick={startVoice}
                  aria-label={t("input.voice")}
                  title={t("input.voice")}
                  disabled={disabled}
                  className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <rect x="9" y="3" width="6" height="11" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                  </svg>
                </button>
              ) : null}
              {/* Send/stop is ONE persistent button whose command, label
                  and icon swap on ``streaming`` — never two sibling
                  elements that mount/unmount. Swapping elements dropped
                  keyboard focus to <body> for mouse-click senders when
                  the stream started or ended (the focused element left
                  the DOM), which is exactly the DataTrigger-on-IsStreaming
                  shape the desktop composer uses on its single Button.
                  ChatGPT-style send state: when something is typed, the
                  circle inverts to the foreground colour so the icon pops
                  without using the accent green; the disabled state stays
                  muted on the same neutral surface as the composer chrome.

                  FIX75 — the STOP state uses the same inverted fill, from
                  the same place: the ``composer-send-fill`` class in
                  globals.css (fill --text-primary, icon --bg-primary,
                  hover --accent-hover). It used to be the error red
                  (--error-solid), which is 2.77:1 against the composer
                  surface in the dark theme (WCAG 1.4.11 wants 3:1) and
                  spent the failure colour on a routine, reversible action.
                  One class for both states so they cannot drift apart
                  again. While streaming, ``stop-halo`` adds a breathing
                  box-shadow ring (colour and spread only — the button's
                  box never moves or resizes, so the target stays under
                  the cursor; static ring under prefers-reduced-motion).
                  The label and the square icon carry "stop" now that
                  the colour does not.

                  The disabled state's inline style references the RAW
                  CSS variables that ``globals.css`` defines under
                  [data-theme="dark"] and the light-mode root.
                  ``--bg-base`` / ``--bg-elevated`` only exist as Tailwind
                  aliases (see tailwind.config.js), so we use
                  ``--bg-surface-hover`` here. */}
              <button
                type="button"
                onClick={streaming ? onStop : submit}
                disabled={!streaming && !canSend}
                aria-label={streaming ? t("input.stop") : t("input.send")}
                className={`focus-ring tap-44 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors duration-150 ${
                  streaming
                    ? "composer-send-fill stop-halo"
                    : canSend
                      ? "composer-send-fill"
                      : "disabled:cursor-not-allowed"
                }`}
                style={
                  streaming || canSend
                    ? undefined
                    : {
                        background: "var(--bg-surface-hover)",
                        color: "var(--text-tertiary)",
                      }
                }
              >
                {streaming ? (
                  <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <rect x="6.5" y="6.5" width="11" height="11" rx="3" />
                  </svg>
                ) : (
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
                )}
              </button>
            </div>
          </div>
        </div>

        <p className="text-center text-[11px] text-text-muted">
          {t("input.footer")}
        </p>
      </div>

      {/* Phase 10 — the cheatsheet the /shortcuts action opens. The
          global ? / Cmd+/ binding opens the copy mounted by the chat
          layout; this one keeps the composer self-contained. */}
      <ShortcutsDialog
        open={shortcutsOpen}
        onClose={() => setShortcutsOpen(false)}
      />
    </div>
  );
}

// Re-exported so callers (like the landing page's drag-and-drop
// wrapper) can stage chips into the SAME bucket the ChatInput
// instance is rendering from.
export { DRAFT_CONV_KEY };

/** Live (non-error) chips in a composer bucket, read from the store now.
 *  FIX126 F1 — every entry path counts against this, not a render snapshot. */
function liveChipCount(bucket: string): number {
  return (useChatStore.getState().pendingFiles[bucket] ?? []).filter(
    (p) => p.status !== "error",
  ).length;
}

// Imperative API for the drag-and-drop wrapper. The conversation
// page's drop handler calls this directly rather than re-implementing
// validation. `chipBucket` controls where the visible PendingFile
// rows live in chatStore — defaults to netConvId, but the landing
// page passes DRAFT_CONV_KEY so the chips render in the same place
// the landing's ChatInput is reading from.
//
// `conv` is the real conversation id (uploads go there on the backend),
// or — on the new-chat page — a resolver that creates it. FIX126 F1: the
// drop is gated by the same per-message cap as the picker and paste, and
// the chips are staged synchronously BEFORE the resolver runs, so a drop
// whose files are all rejected never creates a conversation and two quick
// drops cannot both see the same free slots.
export async function uploadFiles(
  conv: string | (() => Promise<string | null>),
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
    markFileError: (id: string, pid: string, err: string, retryable?: boolean) => void;
    /** Optional so existing callers that don't care about the
     *  background-extraction phase keep compiling unchanged. */
    setFileExtractionStatus?: (
      id: string,
      pid: string,
      extractionStatus: ExtractionStatus,
      unreadableReason?: FileAttachment["unreadable_reason"],
    ) => void;
  },
  chipBucket: string = typeof conv === "string" ? conv : DRAFT_CONV_KEY,
): Promise<void> {
  const { accepted } = admitFilesForMessage(
    files,
    liveChipCount(chipBucket),
    undefined,
    toastError,
  );
  if (accepted.length === 0) return;
  const staged = accepted.map((f) => ({ f, pid: store.addPendingFile(chipBucket, f) }));
  let netConvId: string | null;
  if (typeof conv === "string") {
    netConvId = conv;
  } else {
    try {
      netConvId = await conv();
    } catch {
      netConvId = null;
    }
  }
  if (!netConvId) {
    for (const { pid } of staged) {
      store.markFileError(chipBucket, pid, tOutside("file.err.noConversation"));
    }
    return;
  }
  for (const { f, pid } of staged) {
    const ctrl = beginUpload(pid, netConvId); // FIX127 A1
    if (!ctrl) continue;
    try {
      const row = await api.uploadFile(netConvId, f, {
        signal: ctrl.signal,
        clientId: pid,
        onProgress: (p) => store.updateFileProgress(chipBucket, pid, p),
      });
      endUpload(pid);
      store.markFileReady(chipBucket, pid, row.id, row.extraction_status);
      const onStatus = store.setFileExtractionStatus;
      if (onStatus) {
        const target = netConvId;
        watchExtraction(target, row.id, row.extraction_status, (status, polled) =>
          onStatus(chipBucket, pid, status, polled?.unreadable_reason),
        );
      }
    } catch (err) {
      endUpload(pid);
      if (err instanceof ApiError && err.code === "ABORTED") continue;
      const msg = uploadErrorMessage(err);
      store.markFileError(chipBucket, pid, msg, uploadErrorRetryable(err));
      toastError(msg);
    }
  }
}
