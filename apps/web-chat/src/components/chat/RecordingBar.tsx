"use client";

// RecordingBar — replaces the composer pill while voice input is
// active. Shows:
//
//   ● Recording 0:03   "live transcript preview"  [■ Stop]
//
// The waveform sits before the elapsed-time chip when a live mic
// stream is available; otherwise the CSS-only fallback in
// AudioWaveform takes over so the bar never feels static.

import { AudioWaveform } from "./AudioWaveform";

interface Props {
  transcript: string;
  interimTranscript: string;
  elapsedSec: number;
  /** True while the Whisper round-trip is in flight after stop. */
  isTranscribing: boolean;
  /** Optional live mic MediaStream — drives the waveform analyser. */
  stream?: MediaStream | null;
  onStop: () => void;
}

export function RecordingBar({
  transcript,
  interimTranscript,
  elapsedSec,
  isTranscribing,
  stream,
  onStop,
}: Props) {
  const preview = combinePreview(transcript, interimTranscript);
  return (
    <div
      className="flex items-center gap-3 rounded-3xl border border-status-danger/40 bg-status-danger/5 px-3 py-2.5 shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2.5 shrink-0">
        <span
          className="rec-dot inline-block h-2.5 w-2.5 rounded-full bg-status-danger"
          aria-hidden
        />
        <span className="text-xs font-medium text-text-primary">
          {isTranscribing ? "Transcribing…" : "Recording"}
        </span>
        <span className="text-xs tabular-nums text-text-muted">
          {formatTime(elapsedSec)}
        </span>
        <AudioWaveform stream={stream ?? null} size={18} className="ml-1" />
      </div>
      <div className="min-w-0 flex-1 truncate text-xs text-text-secondary">
        {preview || (
          <span className="text-text-muted">Speak now…</span>
        )}
      </div>
      <button
        type="button"
        onClick={onStop}
        aria-label="Stop recording"
        className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-status-danger text-white transition-colors hover:bg-status-danger/90"
      >
        <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      </button>
    </div>
  );
}

function combinePreview(final: string, interim: string): string {
  const f = final.trim();
  const i = interim.trim();
  if (f && i) return `${f} ${i}`;
  return f || i || "";
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const ss = (s % 60).toString().padStart(2, "0");
  return `${m}:${ss}`;
}
