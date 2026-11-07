// Batch 1 W3 — vision-capability gate for image attachments.
//
// The server catalog ships `capabilities: []string` with "vision" on the
// rows that accept image input. Since FIX27 that string comes from ONE
// source: the gateway's `model_configs.supports_vision` flag (chat-edge
// /models appends "vision" from it) — the same string decides the attach
// button's label, the picker's "Reads images" badge and this gate, so a
// label and a behaviour can no longer disagree. Auto and ids missing from
// the catalog are optimistic (the gateway routes/rejects politely) — do not
// widen the match.
//
// FIX27 §2.2 — a file is NEVER dropped silently. When the active model
// cannot read images the files stay staged, the toast says so and offers
// the way out as ONE click: "Switch to <a vision model>". The send itself
// is blocked by ChatInput.submit (visionBlockedSend) until the model
// changes or the image is removed.

import { toast } from "@/components/ui/Toast";
import { isImageFile } from "@/lib/fileUtil";
import { tOutside } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

const VISION_CAPS = ["vision", "image_input", "multimodal"] as const;

export function hasVisionCap(model: ModelEntry): boolean {
  return VISION_CAPS.some((c) => model.capabilities?.includes(c));
}

/** FIX47 Part C4 — the model can use tools (chat-edge's capability
 *  string for the gateway's supports_tools flag). */
export function hasToolsCap(model: ModelEntry): boolean {
  return Boolean(model.capabilities?.includes("function_calling"));
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

/** The vision-capable model the switch offer names: the first catalog
 *  entry (other than the active one) that reads images. Prefers a
 *  well-known general model when present so the offer is not a codex. */
export function visionAlternative(activeModelId: string | null, models: ModelEntry[]): ModelEntry | null {
  // FIX65 — no model names in code: the offer is the catalogue's first
  // vision-capable chat model THAT THE PICKER OFFERS (a model the admin
  // hid from chat is never proposed), preferring one from the same
  // provider so the switch stays as close to what the user chose as it can.
  const candidates = models.filter(
    (m) => m.id !== activeModelId && hasVisionCap(m) && (!m.model_kind || m.model_kind === "chat") && m.show_in_chat !== false,
  );
  const activeProvider = models.find((m) => m.id === activeModelId)?.provider;
  const sameProvider = activeProvider ? candidates.find((m) => m.provider === activeProvider) : undefined;
  return sameProvider ?? candidates[0] ?? null;
}

/** Shows the "this model can't read images — switch?" toast with the
 *  one-click switch. Returns true when a toast was shown. */
export function offerVisionSwitch(activeModelId: string | null, models: ModelEntry[]): boolean {
  const alt = visionAlternative(activeModelId, models);
  if (alt) {
    toast(tOutside("input.visionUnsupported", { model: alt.name ?? alt.id }), {
      label: tOutside("input.visionSwitchTo", { model: alt.name ?? alt.id }),
      onClick: () => useModelStore.getState().selectModel(alt.id),
    });
  } else {
    toast(tOutside("input.visionUnsupportedNoAlt"));
  }
  return true;
}

/** FIX27: keeps every file (images included) and, when the active model
 *  cannot read images, shows the switch offer instead of dropping them.
 *  Reads the model list via getState() so stale closures (e.g. the
 *  composer's mount-time paste listener) still gate against the CURRENT
 *  catalog. Non-image files never trigger the offer. */
export function gateImagesForVision(
  files: File[],
  activeModelId: string | null,
): File[] {
  const models = useModelStore.getState().models;
  if (modelAcceptsImages(activeModelId, models)) return files;
  if (files.some((f) => isImageFile(f))) offerVisionSwitch(activeModelId, models);
  return files;
}
