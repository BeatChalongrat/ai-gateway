"use client";

// Dictation, once. The chat's composer held all of this inline; an agent's
// workspace dictates the same way, so it lives here and both composers use
// it (AM6): the two recorders and which one is used, the recording counter,
// the transcript landing in the text box, a recorder's error, the
// Ctrl+Shift+V toggle, the recording bar that takes the pill's place, and
// the microphone button.

import { useEffect, useRef, type ReactNode, type RefObject } from "react";

import { toast } from "@/components/ui/Toast";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { useWhisperInput } from "@/hooks/useWhisperInput";
import { VOICE_TOGGLE_EVENT } from "@/lib/composerShortcuts";
import { tOutside, useT } from "@/lib/i18n";
import { useVoiceStore } from "@/stores/voiceStore";

import { RecordingBar } from "./RecordingBar";

export interface Dictation {
  /** A recorder exists in this browser: the microphone is offered. */
  supported: boolean;
  /** Recording, or the recording being turned into text: the bar stands in for the pill. */
  active: boolean;
  start: () => void;
  stop: () => void;
  /** The recording bar while active (ComposerFrame's `above`). */
  bar: ReactNode;
}

interface Options {
  /** What was said is added to the end of what is typed. */
  setText: (update: (current: string) => string) => void;
  textareaRef: RefObject<HTMLTextAreaElement>;
  /** An answer is being written: the shortcut does not start a recording over it. */
  streaming?: boolean;
}

/** Optional haptic on mobile — supported on Android Chrome. */
function buzz() {
  if (typeof navigator !== "undefined" && "vibrate" in navigator) {
    try {
      (navigator as Navigator & { vibrate?: (p: number | number[]) => boolean }).vibrate?.(10);
    } catch {
      /* nothing */
    }
  }
}

export function useDictation({ setText, textareaRef, streaming = false }: Options): Dictation {
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
      const ta = textareaRef.current;
      if (ta) {
        ta.focus();
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      }
    });
  }, [isRecording, voiceTranscript, resetVoiceTranscript, setText, textareaRef]);

  const start = () => {
    buzz();
    if (useWhisperPath) {
      void whisper.start();
    } else {
      webSpeech.start();
    }
  };

  const stop = () => {
    buzz();
    if (useWhisperPath) {
      void whisper.stop();
    } else {
      webSpeech.stop();
    }
  };

  // FIX34 Part D — Ctrl+Shift+V (global shortcut) toggles the recorder. The
  // hook dispatches an event because the recorder lives here.
  const voiceRef = useRef({ start, stop, supported: voiceSupported, recording: isRecording, streaming });
  voiceRef.current = { start, stop, supported: voiceSupported, recording: isRecording, streaming };
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

  const active = isRecording || whisper.isTranscribing;
  return {
    supported: voiceSupported,
    active,
    start,
    stop,
    /* Recording mode replaces the composer pill with a full-width
       recording bar. Same outer container — the decision is purely
       render-level so flex/scroll constraints from the parent don't
       shift. */
    bar: active ? (
      <RecordingBar
        transcript={voiceTranscript}
        interimTranscript={voiceInterim}
        elapsedSec={recordingDuration}
        isTranscribing={whisper.isTranscribing}
        stream={null}
        onStop={stop}
      />
    ) : null,
  };
}

/** The microphone beside send. */
export function DictationButton({ onStart, disabled }: { onStart: () => void; disabled?: boolean }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onStart}
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
  );
}
