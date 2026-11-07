import type { ModelEntry } from "@/lib/types";
import { hasToolsCap, hasVisionCap } from "@/lib/visionGate";

/** Preserve the failed model's input capabilities when offering a switch. */
export function outputLimitAlternative(failedModelId: string, models: ModelEntry[]): ModelEntry | null {
  const failed = models.find((m) => m.id === failedModelId);
  if (!failed) return null;
  const candidates = models.filter((m) =>
    m.id !== failedModelId && m.allowed && m.show_in_chat !== false &&
    (!m.model_kind || m.model_kind === "chat") &&
    (!hasToolsCap(failed) || hasToolsCap(m)) &&
    (!hasVisionCap(failed) || hasVisionCap(m)),
  );
  return candidates.find((m) => m.provider !== failed.provider) ?? candidates[0] ?? null;
}
