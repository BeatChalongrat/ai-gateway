"use client";

// Small banner shown above a media-gen form (Sprint 2A.5) so the user
// knows they've left the chat composer. Switching back to chat is done
// via the adjacent ModelSelector (rendered next to this in ChatInput).

import { Music, Video, Volume2 } from "lucide-react";

import { useT } from "@/lib/i18n";

interface Props {
  kind: "video" | "audio" | "tts";
  modelName: string;
}

const META = {
  video: { icon: Video, labelKey: "media.mode.video" },
  audio: { icon: Music, labelKey: "media.mode.audio" },
  tts: { icon: Volume2, labelKey: "media.mode.tts" },
} as const;

export function MediaModeIndicator({ kind, modelName }: Props) {
  const t = useT();
  const { icon: Icon, labelKey } = META[kind];
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-border bg-bg-secondary px-3 py-1 text-xs text-text-secondary">
      <Icon className="h-4 w-4 text-text-muted" aria-hidden />
      <span className="font-medium text-text-primary">{t(labelKey)}</span>
      <span className="truncate text-text-muted">— {modelName}</span>
    </div>
  );
}
