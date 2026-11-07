// useWhisperInput — MediaRecorder + Whisper proxy fallback.
//
// Used when:
//   • the browser has no SpeechRecognition (Firefox), OR
//   • the user picked "Whisper" in voice settings for higher accuracy
//
// Lifecycle is push-to-talk style: start() opens the mic and begins
// recording, stop() ends the recording, packages the blob, and POSTs
// to /api/v1/voice/transcribe. The transcript lands in the voice
// store the moment the proxy responds — same surface the Web Speech
// hook publishes to, so the ChatInput UI doesn't need to branch.
//
// MediaRecorder mime-type negotiation: not every browser supports
// every codec. We try webm/opus first (Chrome/Edge), then mp4/aac
// (Safari), then fall back to whatever isTypeSupported says.

"use client";

import { tOutside } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useVoiceStore } from "@/stores/voiceStore";

interface UseWhisperInputReturn {
  isSupported: boolean;
  /** True while the upload + Whisper call is in flight, AFTER the
   *  user has stopped recording. Lets the UI show a small "Transcribing…"
   *  state distinct from the recording state. */
  isTranscribing: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
}

const PREFERRED_MIMES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

function pickMimeType(): string {
  if (typeof window === "undefined") return "audio/webm";
  const MR = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder;
  if (!MR || typeof MR.isTypeSupported !== "function") return "audio/webm";
  for (const m of PREFERRED_MIMES) {
    if (MR.isTypeSupported(m)) return m;
  }
  return "audio/webm";
}

export function useWhisperInput(): UseWhisperInputReturn {
  const inputLang = useVoiceStore((s) => s.inputLang);
  const startRecording = useVoiceStore((s) => s.startRecording);
  const stopRecording = useVoiceStore((s) => s.stopRecording);
  const appendTranscript = useVoiceStore((s) => s.appendTranscript);
  const setError = useVoiceStore((s) => s.setError);

  const [isSupported, setIsSupported] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const hasRecorder =
      typeof (window as unknown as { MediaRecorder?: unknown }).MediaRecorder !==
      "undefined";
    const hasMedia = !!navigator?.mediaDevices?.getUserMedia;
    setIsSupported(hasRecorder && hasMedia);
  }, []);

  const cleanup = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const start = useCallback(async () => {
    if (!isSupported) {
      setError(tOutside("voice.err.recNotSupported"));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickMimeType();
      const rec = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.start(250); // emit chunks every 250ms so a hard-stop captures recent audio
      recorderRef.current = rec;
      startRecording();
    } catch (err) {
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError(
          tOutside("voice.err.micDenied"),
        );
      } else {
        setError(err instanceof Error ? err.message : tOutside("voice.err.startFailed"));
      }
      cleanup();
    }
  }, [isSupported, startRecording, setError, cleanup]);

  const stop = useCallback(async () => {
    const rec = recorderRef.current;
    if (!rec) {
      stopRecording();
      cleanup();
      return;
    }
    // Wait for the recorder to fully flush its buffer before we
    // upload. Wrapping the async stop in a promise keeps the caller
    // simple — they just await one operation.
    const blob = await new Promise<Blob | null>((resolve) => {
      rec.onstop = () => {
        if (chunksRef.current.length === 0) {
          resolve(null);
          return;
        }
        const mime = rec.mimeType || "audio/webm";
        resolve(new Blob(chunksRef.current, { type: mime }));
      };
      try {
        rec.stop();
      } catch {
        resolve(null);
      }
    });
    stopRecording();
    cleanup();
    if (!blob || blob.size === 0) {
      setError(tOutside("voice.err.noAudio"));
      return;
    }
    setIsTranscribing(true);
    try {
      const text = await api.transcribeAudio(blob, inputLang);
      if (text.trim()) appendTranscript(text.trim());
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setError(
          tOutside("voice.err.whisperNotConfigured"),
        );
      } else {
        setError(err instanceof Error ? err.message : tOutside("voice.err.transcribeFailed"));
      }
    } finally {
      setIsTranscribing(false);
    }
  }, [inputLang, stopRecording, cleanup, appendTranscript, setError]);

  // Drop the stream on unmount so the OS mic indicator goes away.
  useEffect(() => {
    return () => {
      try {
        recorderRef.current?.stop();
      } catch {
        /* nothing */
      }
      cleanup();
    };
  }, [cleanup]);

  return { isSupported, isTranscribing, start, stop };
}
