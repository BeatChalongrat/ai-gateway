"use client";

// AudioWaveform — compact 7-bar visualization rendered while the user
// is recording.
//
// Two modes:
//
//   "live"   — uses Web Audio API (AnalyserNode) against the
//              MediaStream returned by getUserMedia. Bars bounce in
//              proportion to the per-frequency-bin energy.
//
//   "css"    — pure CSS keyframe animation. Used when AudioContext
//              isn't available OR no live MediaStream was passed.
//
// The component falls back to the css mode transparently — the
// recording-bar UI doesn't need to know which one is active.

import { useEffect, useRef } from "react";

const BAR_COUNT = 7;

interface Props {
  /** Optional live mic stream. When provided + AudioContext is
   *  available, drives the bars from analyser data. When omitted, a
   *  CSS keyframe animation runs instead. */
  stream?: MediaStream | null;
  /** Visual size — height in px (width is computed as BAR_COUNT * 4px). */
  size?: number;
  className?: string;
}

export function AudioWaveform({ stream, size = 22, className = "" }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    if (typeof window === "undefined") return;
    const Ctor =
      (window as unknown as { AudioContext?: typeof AudioContext })
        .AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!stream || !Ctor) return; // CSS fallback handles the visual.

    let ctx: AudioContext | null = null;
    let analyser: AnalyserNode | null = null;
    let src: MediaStreamAudioSourceNode | null = null;
    let raf = 0;
    try {
      ctx = new Ctor();
      analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      src = ctx.createMediaStreamSource(stream);
      src.connect(analyser);
    } catch {
      return;
    }
    const data = new Uint8Array(analyser.frequencyBinCount);
    const bars = Array.from(
      container.querySelectorAll<HTMLSpanElement>("[data-bar]"),
    );
    // Pick BAR_COUNT evenly-spaced frequency bins. Skipping the very
    // low bins removes the DC offset bump that pegs the first bar at
    // full height.
    const step = Math.max(
      1,
      Math.floor((analyser.frequencyBinCount - 2) / BAR_COUNT),
    );

    const tick = () => {
      analyser!.getByteFrequencyData(data);
      bars.forEach((bar, i) => {
        const v = data[2 + i * step] ?? 0;
        // 0–255 → 12 %–100 % scale; floor at 12 % so an idle mic
        // still shows a baseline pulse instead of a dead bar.
        const pct = Math.max(12, Math.min(100, (v / 255) * 100));
        bar.style.height = pct + "%";
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      try {
        src?.disconnect();
        analyser?.disconnect();
        void ctx?.close();
      } catch {
        /* nothing */
      }
    };
  }, [stream]);

  const cssOnly = !stream;

  return (
    <div
      ref={ref}
      className={`inline-flex items-end gap-[2px] ${className}`}
      style={{ height: size, width: BAR_COUNT * 4 + (BAR_COUNT - 1) * 2 }}
      aria-hidden
    >
      {Array.from({ length: BAR_COUNT }, (_, i) => (
        <span
          key={i}
          data-bar
          className={
            cssOnly
              ? "audio-bar audio-bar-css inline-block w-[3px] rounded-sm bg-accent"
              : "inline-block w-[3px] rounded-sm bg-accent transition-[height] duration-75 ease-out"
          }
          style={
            cssOnly
              ? {
                  animationDelay: `${i * 80}ms`,
                  height: "30%",
                }
              : { height: "20%" }
          }
        />
      ))}
    </div>
  );
}
