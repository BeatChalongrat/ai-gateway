// FIX65 — the "switch to …" offer names a model the picker actually
// offers, chosen from the catalogue and never from a name in code.
//
// Seen live before this rule: qwen (self-hosted, vision off) got the offer
// "Switch to GPT 5.1 2025 11 13" — a row the admin had just hidden from
// chat with the floor. The offer must come from shown, vision-capable
// chat models, preferring the same provider as the model in use.
import { describe, expect, it } from "vitest";
import type { ModelEntry } from "@/lib/types";
import { visionAlternative } from "@/lib/visionGate";

function model(partial: Partial<ModelEntry> & { id: string }): ModelEntry {
  return {
    name: partial.id,
    provider: "vendor",
    description: "",
    context_window: 128000,
    capabilities: [],
    icon: "",
    category: "fast" as ModelEntry["category"],
    allowed: true,
    ...partial,
  };
}

const ACTIVE = model({ id: "own-blind", provider: "sranai", self_hosted: true });
const HIDDEN_VISION = model({ id: "hidden-vision", capabilities: ["vision"], show_in_chat: false });
const SHOWN_VISION = model({ id: "shown-vision", capabilities: ["vision"], show_in_chat: true });
const SAME_PROVIDER_VISION = model({ id: "own-sees", provider: "sranai", self_hosted: true, capabilities: ["vision"] });
const VIDEO_VISION = model({ id: "video-model", capabilities: ["vision"], model_kind: "video" });
const TEXT_ONLY = model({ id: "text-only" });

describe("FIX65 — visionAlternative", () => {
  it("never proposes a model hidden from the picker, even when it is first in the list", () => {
    expect(visionAlternative(ACTIVE.id, [ACTIVE, HIDDEN_VISION, SHOWN_VISION])?.id).toBe("shown-vision");
  });

  it("prefers a vision-capable model from the same provider", () => {
    expect(visionAlternative(ACTIVE.id, [ACTIVE, SHOWN_VISION, SAME_PROVIDER_VISION])?.id).toBe("own-sees");
  });

  it("skips media models and the active model itself", () => {
    expect(visionAlternative(ACTIVE.id, [ACTIVE, VIDEO_VISION, TEXT_ONLY])).toBeNull();
    expect(visionAlternative(SHOWN_VISION.id, [SHOWN_VISION, TEXT_ONLY])).toBeNull();
  });

  it("an entry with no show_in_chat field (older chat-edge) still counts as shown", () => {
    expect(visionAlternative(ACTIVE.id, [ACTIVE, model({ id: "legacy-vision", capabilities: ["vision"] })])?.id).toBe("legacy-vision");
  });
});
