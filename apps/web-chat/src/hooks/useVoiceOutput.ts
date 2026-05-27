// useVoiceOutput — text-to-speech for assistant messages.
//
// Two backends, picked per-call via the voice store:
//
//   "browser" — window.speechSynthesis. Free, zero-latency, no server
//               round-trip, but quality varies by OS. Default.
//
//   "openai"  — POST to /voice/synthesize, receive an mp3 stream,
//               play via HTMLAudioElement. Higher quality, premium
//               voices (alloy/echo/...), latency = one round-trip.
//
// Markdown is stripped before either path runs — the model emits
// headings, fences, bold etc. that read terribly out loud. The
// strip is intentionally lossy: it keeps the human-readable content
// and discards code blocks rather than spelling out every backtick.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useVoiceStore } from "@/stores/voiceStore";

interface UseVoiceOutputReturn {
  isSupported: boolean;
  speakingMessageId: string | null;
  voices: SpeechSynthesisVoice[];
  speak: (text: string, messageId: string) => Promise<void>;
  stop: () => void;
}

// Pull voices from speechSynthesis with a small delay on first read
// — Chrome populates them asynchronously after the page loads.
export function useVoiceOutput(): UseVoiceOutputReturn {
  const outputMode = useVoiceStore((s) => s.outputMode);
  const browserVoiceName = useVoiceStore((s) => s.browserVoiceName);
  const openaiVoice = useVoiceStore((s) => s.openaiVoice);
  const rate = useVoiceStore((s) => s.rate);
  const speakingMessageId = useVoiceStore((s) => s.speakingMessageId);
  const setSpeakingMessage = useVoiceStore((s) => s.setSpeakingMessage);

  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [isSupported, setIsSupported] = useState(false);

  // The active audio element (openai path) — kept in a ref so the
  // stop() callback can pause / reset without state churn.
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const ss = window.speechSynthesis;
    if (!ss) {
      setIsSupported(false);
      return;
    }
    setIsSupported(true);
    const sync = () => setVoices(ss.getVoices());
    sync();
    ss.onvoiceschanged = sync;
    return () => {
      ss.onvoiceschanged = null;
    };
  }, []);

  const stop = useCallback(() => {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    setSpeakingMessage(null);
  }, [setSpeakingMessage]);

  const speak = useCallback(
    async (rawText: string, messageId: string) => {
      // If something is already playing, stop it. If the caller is
      // tapping the same message twice, treat the second tap as a
      // stop — matches the toggle behaviour the UI advertises.
      if (speakingMessageId === messageId) {
        stop();
        return;
      }
      stop();

      const text = stripMarkdownForSpeech(rawText);
      if (!text.trim()) return;

      if (outputMode === "openai") {
        try {
          setSpeakingMessage(messageId);
          const blob = await api.synthesizeSpeech({
            text: truncateForTTS(text),
            voice: openaiVoice,
            speed: rate,
          });
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.onended = () => {
            URL.revokeObjectURL(url);
            setSpeakingMessage(null);
            audioRef.current = null;
          };
          audio.onerror = () => {
            URL.revokeObjectURL(url);
            setSpeakingMessage(null);
            audioRef.current = null;
          };
          audioRef.current = audio;
          await audio.play();
        } catch (err) {
          setSpeakingMessage(null);
          audioRef.current = null;
          if (err instanceof ApiError && err.status === 503) {
            // Fall back to browser TTS so the user still hears
            // something instead of a dead button.
            speakBrowser(text, browserVoiceName, rate, voices, messageId, setSpeakingMessage);
            return;
          }
          // Otherwise surface nothing — the click was a soft action.
        }
        return;
      }
      speakBrowser(text, browserVoiceName, rate, voices, messageId, setSpeakingMessage);
    },
    [
      outputMode,
      browserVoiceName,
      openaiVoice,
      rate,
      voices,
      speakingMessageId,
      stop,
      setSpeakingMessage,
    ],
  );

  // Cancel on unmount so a navigate-away kills the playback.
  useEffect(() => {
    return () => stop();
  }, [stop]);

  return { isSupported, speakingMessageId, voices, speak, stop };
}

// ---------------------------------------------------------------------------
// Browser-native speech path
// ---------------------------------------------------------------------------

function speakBrowser(
  text: string,
  voiceName: string | null,
  rate: number,
  voices: SpeechSynthesisVoice[],
  messageId: string,
  setSpeakingMessage: (id: string | null) => void,
) {
  if (typeof window === "undefined") return;
  const ss = window.speechSynthesis;
  if (!ss) return;

  // Split into sentence-ish chunks. speechSynthesis on Chrome
  // truncates utterances over ~200 chars on macOS / iOS — chunking
  // also lets us cancel cleanly mid-message.
  const chunks = chunkForSpeech(text);
  if (chunks.length === 0) return;

  const voice =
    (voiceName && voices.find((v) => v.name === voiceName)) ||
    voices.find((v) => v.lang.startsWith("en")) ||
    voices[0] ||
    null;

  setSpeakingMessage(messageId);
  let i = 0;
  const playNext = () => {
    if (i >= chunks.length) {
      setSpeakingMessage(null);
      return;
    }
    const u = new SpeechSynthesisUtterance(chunks[i]);
    if (voice) u.voice = voice;
    u.rate = rate;
    u.onend = () => {
      i++;
      playNext();
    };
    u.onerror = () => {
      setSpeakingMessage(null);
    };
    ss.speak(u);
  };
  playNext();
}

// ---------------------------------------------------------------------------
// Text helpers
// ---------------------------------------------------------------------------

// stripMarkdownForSpeech keeps the readable content but trims out
// formatting noise that reads badly. Code blocks are replaced with a
// stand-in "(code block)" line so the listener still knows
// something technical was elided.
function stripMarkdownForSpeech(s: string): string {
  return (
    s
      // Fenced code blocks → placeholder.
      .replace(/```[\s\S]*?```/g, " (code block) ")
      // Inline code → keep the contents, drop the ticks.
      .replace(/`([^`]+)`/g, "$1")
      // Images / links → keep the link text, drop the URL.
      .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      // Headings — drop the #s but keep the title.
      .replace(/^#{1,6}\s+/gm, "")
      // Bold / italic markers.
      .replace(/[*_]{1,3}([^*_]+)[*_]{1,3}/g, "$1")
      // Blockquote markers at start of line.
      .replace(/^\s{0,3}>\s?/gm, "")
      // Citation pills produced by the RAG renderer.
      .replace(/\[Source:\s*([^\]]+)\]/g, "(source $1)")
      // Collapse runs of whitespace.
      .replace(/\s+/g, " ")
      .trim()
  );
}

const TTS_LIMIT = 4096;

function truncateForTTS(s: string): string {
  if (s.length <= TTS_LIMIT) return s;
  return s.slice(0, TTS_LIMIT - 1) + "…";
}

// chunkForSpeech splits into ~200-char sentence groupings. Picks the
// largest group that ends at sentence punctuation while still under
// the limit; falls back to a hard cut if a single sentence is too
// long.
function chunkForSpeech(s: string): string[] {
  const HARD = 180;
  const out: string[] = [];
  let i = 0;
  while (i < s.length) {
    const remain = s.length - i;
    if (remain <= HARD) {
      out.push(s.slice(i).trim());
      break;
    }
    // Scan for the last sentence boundary within the window.
    const slice = s.slice(i, i + HARD);
    const lastBoundary = Math.max(
      slice.lastIndexOf(". "),
      slice.lastIndexOf("? "),
      slice.lastIndexOf("! "),
      slice.lastIndexOf("\n"),
    );
    const cut = lastBoundary > 40 ? lastBoundary + 1 : HARD;
    const chunk = s.slice(i, i + cut).trim();
    if (chunk) out.push(chunk);
    i += cut;
  }
  return out.filter((c) => c.length > 0);
}
