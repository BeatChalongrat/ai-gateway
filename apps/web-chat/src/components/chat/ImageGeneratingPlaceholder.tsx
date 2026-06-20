"use client";

// Loading placeholder shown in place of the typing dots while the
// backend is generating an image (the `image_generating` SSE event).
// A theme-aware shimmer sweeps a 256px square — the same footprint the
// generated thumbnail will occupy — so the swap to the real image
// (via `message_files`) doesn't jump the layout. CSS-only animation:
// reuses the global .skeleton shimmer + Tailwind's animate-pulse.

import { ImageIcon } from "lucide-react";

export function ImageGeneratingPlaceholder() {
  return (
    <div
      className="relative mt-1 h-64 w-64 max-w-full overflow-hidden rounded-lg border border-border bg-bg-secondary"
      role="status"
      aria-label="กำลังสร้างรูปภาพ"
    >
      {/* Shimmer layer — square corners; the wrapper clips them. */}
      <div className="skeleton absolute inset-0 rounded-none" aria-hidden />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4">
        <ImageIcon
          className="h-8 w-8 animate-pulse text-text-tertiary"
          aria-hidden
        />
        <div className="text-center">
          <p className="shimmer-text text-sm font-medium">
            กำลังสร้างรูปภาพ...
          </p>
          <p className="mt-0.5 text-xs text-text-muted">อาจใช้เวลาสักครู่</p>
        </div>
      </div>
    </div>
  );
}
