"use client";

// Small banner shown above a media-gen form (Sprint 2A.5) so the user
// knows they've left the chat composer. Switching back to chat is done
// via the adjacent ModelSelector (rendered next to this in ChatInput).

import { Music, Video, Volume2 } from "lucide-react";

interface Props {
  kind: "video" | "audio" | "tts";
  modelName: string;
}

const META = {
  video: { icon: Video, label: "Video Generation Mode" },
  audio: { icon: Music, label: "Music Generation Mode" },
  tts: { icon: Volume2, label: "Text-to-Speech Mode" },
} as const;

export function MediaModeIndicator({ kind, modelName }: Props) {
  const { icon: Icon, label } = META[kind];
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-border bg-bg-secondary px-3 py-1 text-xs text-text-secondary">
      <Icon className="h-4 w-4 text-text-tertiary" aria-hidden />
      <span className="font-medium text-text-primary">{label}</span>
      <span className="truncate text-text-muted">— {modelName}</span>
    </div>
  );
}
