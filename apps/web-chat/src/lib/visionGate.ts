// Batch 1 W3 — vision-capability gate for image attachments.
//
// The server catalog already ships `capabilities: []string` with
// "vision" on the rows that accept image input; web just never checked
// it, so a user could attach an image to a text-only model and get an
// opaque provider error. This mirrors the desktop's test — see
// ChatEdgeModels.SupportsVision (vision|image_input|multimodal) and
// CurrentModelSupportsVision (Auto and unknown catalog entries are
// optimistic: the gateway rejects politely) — do not widen the match.
//
// One gate covers every entry path: the composer's picker + paste run
// through ChatInput.handleFiles, and both pages' drag-and-drop zones
// call gateImagesForVision before uploadFiles.

import { toast } from "@/components/ui/Toast";
import { isImageFile } from "@/lib/fileUtil";
import { tOutside } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

const VISION_CAPS = ["vision", "image_input", "multimodal"] as const;

function hasVisionCap(model: ModelEntry): boolean {
  return VISION_CAPS.some((c) => model.capabilities?.includes(c));
}

/** True when the active model (or Auto) accepts image input. Auto,
 *  a null id, and ids missing from the catalog are all optimistic —
 *  same contract as desktop's CurrentModelSupportsVision. */
export function modelAcceptsImages(
  activeModelId: string | null,
  models: ModelEntry[],
): boolean {
  if (!activeModelId || activeModelId === AUTO_MODEL_ID) return true;
  const model = models.find((m) => m.id === activeModelId);
  return model ? hasVisionCap(model) : true;
}

/** Filters image files out when the active model can't read them,
 *  toasting once with a concrete fix (a vision-capable model from the
 *  catalog) rather than a bare rejection. Non-image files pass through
 *  untouched. Reads the model list via getState() so stale closures
 *  (e.g. the composer's mount-time paste listener) still gate against
 *  the CURRENT catalog. */
export function gateImagesForVision(
  files: File[],
  activeModelId: string | null,
): File[] {
  const models = useModelStore.getState().models;
  if (modelAcceptsImages(activeModelId, models)) return files;
  const kept = files.filter((f) => !isImageFile(f));
  if (kept.length !== files.length) {
    const alt = models.find((m) => m.id !== activeModelId && hasVisionCap(m));
    toast(
      alt
        ? tOutside("input.visionUnsupported", { model: alt.name ?? alt.id })
        : tOutside("input.visionUnsupportedNoAlt"),
    );
  }
  return kept;
}
