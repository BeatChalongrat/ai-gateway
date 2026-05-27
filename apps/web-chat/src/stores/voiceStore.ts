// Voice settings + transient recording state.
//
// Two responsibilities split across one store:
//
//   Settings (persisted via localStorage)
//     - inputMethod:  "web-speech" (browser) | "whisper" (OpenAI proxy)
//     - inputLang:    "auto" | "en-US" | "th-TH" | ...
//     - outputMode:   "browser" (SpeechSynthesis) | "openai" (TTS proxy)
//     - voice:        SpeechSynthesisVoice name (browser) OR OpenAI voice
//     - rate:         0.5 – 2.0
//     - autoRead:     speak every new AI reply when streaming completes
//
//   Recording state (in-memory only)
//     - isRecording, recordingDuration, transcript, interimTranscript
//     - error string surfaced for the recording-bar UI
//
// The hooks (useVoiceInput / useVoiceOutput / useWhisperInput) push
// transcript chunks into this store so the ChatInput recording bar
// can read them without subscribing to a hook tree.

"use client";

import { create } from "zustand";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type VoiceInputMethod = "web-speech" | "whisper";
export type VoiceOutputMode = "browser" | "openai";

// OpenAI's six documented voices. Keeping this in sync with the
// backend allow-list in handler/voice.go.
export const OPENAI_VOICES = [
  "alloy",
  "echo",
  "fable",
  "onyx",
  "nova",
  "shimmer",
] as const;
export type OpenAIVoice = (typeof OPENAI_VOICES)[number];

interface VoiceSettings {
  inputMethod: VoiceInputMethod;
  inputLang: string; // "auto" | BCP-47 tag
  outputMode: VoiceOutputMode;
  // Active browser voice (matched by SpeechSynthesisVoice.name) when
  // outputMode is "browser". Null → first English voice / system default.
  browserVoiceName: string | null;
  openaiVoice: OpenAIVoice;
  rate: number;
  autoRead: boolean;
}

interface VoiceState extends VoiceSettings {
  // Recording — ephemeral.
  isRecording: boolean;
  recordingDuration: number;
  transcript: string;
  interimTranscript: string;
  error: string | null;
  // ID of the assistant message currently being spoken (null when
  // nothing is playing). Used by MessageBubble to flip the speaker
  // icon to a stop state.
  speakingMessageId: string | null;

  // Settings mutators (persist on every change).
  setInputMethod: (m: VoiceInputMethod) => void;
  setInputLang: (l: string) => void;
  setOutputMode: (m: VoiceOutputMode) => void;
  setBrowserVoice: (name: string | null) => void;
  setOpenAIVoice: (v: OpenAIVoice) => void;
  setRate: (r: number) => void;
  setAutoRead: (v: boolean) => void;

  // Recording mutators (transient).
  startRecording: () => void;
  stopRecording: () => void;
  setRecordingDuration: (s: number) => void;
  setTranscript: (final: string, interim?: string) => void;
  appendTranscript: (chunk: string) => void;
  resetTranscript: () => void;
  setError: (e: string | null) => void;
  setSpeakingMessage: (id: string | null) => void;
}

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

const STORAGE_KEY = "sran-webchat-voice-settings";

function defaultSettings(): VoiceSettings {
  return {
    inputMethod: "web-speech",
    inputLang: "auto",
    outputMode: "browser",
    browserVoiceName: null,
    openaiVoice: "alloy",
    rate: 1.0,
    autoRead: false,
  };
}

function readSettings(): VoiceSettings {
  if (typeof window === "undefined") return defaultSettings();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultSettings();
    const parsed = JSON.parse(raw) as Partial<VoiceSettings>;
    return { ...defaultSettings(), ...parsed };
  } catch {
    return defaultSettings();
  }
}

function writeSettings(s: VoiceSettings) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* quota / disabled storage */
  }
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

const initial = readSettings();

export const useVoiceStore = create<VoiceState>((set, get) => ({
  ...initial,

  isRecording: false,
  recordingDuration: 0,
  transcript: "",
  interimTranscript: "",
  error: null,
  speakingMessageId: null,

  setInputMethod(m) {
    set({ inputMethod: m });
    writeSettings(extractSettings(get()));
  },
  setInputLang(l) {
    set({ inputLang: l });
    writeSettings(extractSettings(get()));
  },
  setOutputMode(m) {
    set({ outputMode: m });
    writeSettings(extractSettings(get()));
  },
  setBrowserVoice(name) {
    set({ browserVoiceName: name });
    writeSettings(extractSettings(get()));
  },
  setOpenAIVoice(v) {
    set({ openaiVoice: v });
    writeSettings(extractSettings(get()));
  },
  setRate(r) {
    set({ rate: r });
    writeSettings(extractSettings(get()));
  },
  setAutoRead(v) {
    set({ autoRead: v });
    writeSettings(extractSettings(get()));
  },

  startRecording() {
    set({
      isRecording: true,
      recordingDuration: 0,
      transcript: "",
      interimTranscript: "",
      error: null,
    });
  },
  stopRecording() {
    set({ isRecording: false, interimTranscript: "" });
  },
  setRecordingDuration(s) {
    set({ recordingDuration: s });
  },
  setTranscript(final, interim) {
    set({ transcript: final, interimTranscript: interim ?? "" });
  },
  appendTranscript(chunk) {
    set((s) => {
      const sep = s.transcript && !s.transcript.endsWith(" ") ? " " : "";
      return {
        transcript: s.transcript + sep + chunk,
        interimTranscript: "",
      };
    });
  },
  resetTranscript() {
    set({ transcript: "", interimTranscript: "" });
  },
  setError(e) {
    set({ error: e });
  },
  setSpeakingMessage(id) {
    set({ speakingMessageId: id });
  },
}));

function extractSettings(s: VoiceState): VoiceSettings {
  return {
    inputMethod: s.inputMethod,
    inputLang: s.inputLang,
    outputMode: s.outputMode,
    browserVoiceName: s.browserVoiceName,
    openaiVoice: s.openaiVoice,
    rate: s.rate,
    autoRead: s.autoRead,
  };
}
