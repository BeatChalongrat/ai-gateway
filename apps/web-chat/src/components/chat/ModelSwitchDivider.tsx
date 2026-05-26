"use client";

// Inline divider rendered between two assistant messages when the
// conversation's model changed. The label uses the new model's
// friendly name when known, falling back to the raw id.

import { useModelStore } from "@/stores/modelStore";

import { AssistantAvatar } from "./AssistantAvatar";

interface Props {
  newModelId: string;
}

export function ModelSwitchDivider({ newModelId }: Props) {
  const model = useModelStore((s) => s.getModelById(newModelId));
  const label = model?.name ?? newModelId;
  return (
    <div className="my-2 flex w-full items-center gap-2 px-2 text-[11px] text-text-muted">
      <span className="h-px flex-1 bg-border" />
      <AssistantAvatar modelId={newModelId} size={14} />
      <span className="whitespace-nowrap">Model changed to {label}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
