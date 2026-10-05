// useVoiceInput — thin React wrapper around the Web Speech API.
//
// Why a hook (and not a service singleton)? The SpeechRecognition
// object owns event listeners tied to React render lifecycle, and
// the recording bar is a per-route concern — letting the hook clean
// up on unmount keeps the API simple for the caller.
//
// Lifecycle:
//   start()  → builds + starts a recognition instance, wires events.
//   onresult → split final vs interim transcripts, push into store.
//   onerror  → set error, stop.
//   onend    → if the user didn't stop, restart (browsers auto-end
//              after silence; continuous=true alone isn't enough).
//   stop()   → flag "userStopped" so onend doesn't re-arm, abort.
//
// Browsers supported: Chrome, Edge, Safari (with prefix). Firefox
// has no implementation as of 2026 → isSupported is false, the UI
// hides the mic button and offers Whisper fallback (useWhisperInput).

"use client";

import { tOutside } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState } from "react";

import { useVoiceStore } from "@/stores/voiceStore";

// The DOM lib's SpeechRecognition types live behind a webkit prefix
// in TS lib.dom.d.ts. We pull the constructors with a minimal local
// type so this hook compiles without DOM-lib-augmenting boilerplate.
interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionInstance;
}

interface SpeechRecognitionInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechRecognitionResultEventLike) => void) | null;
  onerror: ((e: { error: string; message?: string }) => void) | null;
  onend: (() => void) | null;
}

interface SpeechRecognitionResultEventLike {
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }> & { length: number };
  resultIndex: number;
}

function getRecognition(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

interface UseVoiceInputReturn {
  isSupported: boolean;
  start: () => void;
  stop: () => void;
}

export function useVoiceInput(): UseVoiceInputReturn {
  const inputLang = useVoiceStore((s) => s.inputLang);
  const startRecording = useVoiceStore((s) => s.startRecording);
  const stopRecording = useVoiceStore((s) => s.stopRecording);
  const setTranscript = useVoiceStore((s) => s.setTranscript);
  const setError = useVoiceStore((s) => s.setError);

  // Live refs (don't trigger re-renders, survive across re-renders).
  const recRef = useRef<SpeechRecognitionInstance | null>(null);
  const userStoppedRef = useRef(false);
  const finalRef = useRef("");

  const [isSupported, setIsSupported] = useState(false);
  useEffect(() => {
    setIsSupported(!!getRecognition());
  }, []);

  // Stop is intentionally idempotent — multiple calls during teardown
  // (effect cleanup + user-click race) should be a no-op.
  const stop = useCallback(() => {
    userStoppedRef.current = true;
    try {
      recRef.current?.stop();
    } catch {
      /* already stopped */
    }
    recRef.current = null;
    stopRecording();
  }, [stopRecording]);

  const start = useCallback(() => {
    const Ctor = getRecognition();
    if (!Ctor) {
      setError(tOutside("voice.err.notSupported"));
      return;
    }
    // Reset every ref + store state so a second recording doesn't
    // inherit the previous session's transcript.
    finalRef.current = "";
    userStoppedRef.current = false;
    startRecording();

    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = inputLang === "auto" ? navigatorLang() : inputLang;

    rec.onresult = (e) => {
      // Walk every NEW result since the previous event.
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const text = r[0].transcript;
        if (r.isFinal) {
          // Append with a single space if needed; trim runs once we
          // commit so the textarea doesn't accumulate double spaces.
          const sep = finalRef.current && !finalRef.current.endsWith(" ") ? " " : "";
          finalRef.current = finalRef.current + sep + text;
        } else {
          interim += text;
        }
      }
      setTranscript(finalRef.current.trim(), interim.trim());
    };

    rec.onerror = (e) => {
      // ``no-speech`` and ``aborted`` are routine — silently restart
      // for no-speech (the user hasn't said anything yet), surface
      // anything else.
      if (e.error === "no-speech" || e.error === "aborted") return;
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setError(
          tOutside("voice.err.micDenied"),
        );
        stop();
        return;
      }
      setError(tOutside("voice.err.generic", { error: e.error }));
    };

    rec.onend = () => {
      // Browsers auto-end after ~5s of silence even with continuous=true.
      // Re-arm unless the user pressed Stop.
      if (userStoppedRef.current) return;
      try {
        rec.start();
      } catch {
        // start() throws "already started" sometimes — safe to ignore.
      }
    };

    try {
      rec.start();
      recRef.current = rec;
    } catch (err) {
      setError(err instanceof Error ? err.message : tOutside("voice.err.startFailed"));
      stopRecording();
    }
  }, [inputLang, startRecording, stopRecording, setError, setTranscript, stop]);

  // Hard-stop on unmount so a forgotten recording doesn't keep
  // running after the user navigates away.
  useEffect(() => {
    return () => {
      try {
        recRef.current?.abort();
      } catch {
        /* nothing */
      }
    };
  }, []);

  return { isSupported, start, stop };
}

function navigatorLang(): string {
  if (typeof navigator === "undefined") return "en-US";
  return navigator.language || "en-US";
}
