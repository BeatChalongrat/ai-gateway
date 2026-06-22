"use client";

// Loading placeholder shown in place of the typing dots while the
// backend is generating a video (the `video_generating` SSE event).
// A theme-aware shimmer sweeps a 320x180 (16:9) rectangle — the same
// footprint the player will occupy — so the swap to the real video (via
// `message_files`) doesn't jump the layout. Indeterminate: video gen
// emits no progress %, just a started marker. CSS-only animation: reuses
// the global .skeleton shimmer + Tailwind's animate-pulse. Mirrors
// ImageGeneratingPlaceholder.

import { Video } from "lucide-react";

export function VideoGeneratingPlaceholder() {
  return (
    <div
      className="relative mt-1 h-[180px] w-[320px] max-w-full overflow-hidden rounded-lg border border-border bg-bg-secondary"
      role="status"
      aria-label="กำลังสร้างวิดีโอ"
    >
      {/* Shimmer layer — square corners; the wrapper clips them. */}
      <div className="skeleton absolute inset-0 rounded-none" aria-hidden />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4">
        <Video className="h-8 w-8 animate-pulse text-text-tertiary" aria-hidden />
        <div className="text-center">
          <p className="shimmer-text text-sm font-medium">
            กำลังสร้างวิดีโอ...
          </p>
          <p className="mt-0.5 text-xs text-text-muted">อาจใช้เวลาสักครู่</p>
        </div>
      </div>
    </div>
  );
}
