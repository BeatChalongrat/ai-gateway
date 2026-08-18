// Models-catalog contract test — the additive-only rule, enforced.
//
// The /api/v1/models contract may only ever GROW: servers add fields and
// capability strings; clients must ignore what they don't know, and a
// model with no ``reasoning`` block is simply not reasoning-capable.
// This test feeds the picker a RAW server response containing fields and
// capability strings this client has never heard of and pins that it
// parses, renders, and derives capabilities correctly — so a server-side
// catalog addition can never require a client release.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { setConversationReasoning: vi.fn(), uploadFile: vi.fn() },
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ModelEntry } from "@/lib/types";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ModelSelector } from "./ModelSelector";

// A server response from THE FUTURE: unknown top-level fields, unknown
// per-model fields, unknown capability strings, an unknown reasoning
// sub-field — exactly what an old installed client will one day receive.
const FUTURE_RESPONSE = JSON.parse(`{
  "models": [
    {
      "id": "gpt-6-preview",
      "name": "GPT-6 Preview",
      "provider": "openai",
      "description": "A model from the future",
      "context_window": 400000,
      "capabilities": ["streaming", "function_calling", "quantum_entanglement", "telepathy"],
      "icon": "",
      "category": "flagship",
      "allowed": true,
      "input_price_per_1m": 1.25,
      "output_price_per_1m": 5,
      "reasoning": {
        "supported": true,
        "modes": ["off", "auto", "galaxy_brain"],
        "default_mode": "auto",
        "summary_supported": true,
        "hologram_supported": true
      },
      "model_kind": "chat",
      "carbon_footprint_grams": 12.5
    },
    {
      "id": "plain-model",
      "name": "Plain Model",
      "provider": "openai",
      "description": "No reasoning block at all",
      "context_window": 8000,
      "capabilities": ["streaming"],
      "icon": "",
      "category": "fast",
      "allowed": true
    }
  ],
  "catalog_generation": 42
}`);

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

function openPickerWithFutureResponse() {
  // The api layer does nothing but JSON.parse — so casting the raw
  // payload is exactly what production does with a newer server.
  const models = FUTURE_RESPONSE.models as ModelEntry[];
  useModelStore.setState({ models, loaded: true, loading: false });
  render(<ModelSelector activeModelId={AUTO_MODEL_ID} onPick={vi.fn()} />);
  fireEvent.click(screen.getByRole("button"));
}

describe("models contract: additive-only tolerance", () => {
  it("renders models carrying unknown fields and capability strings", () => {
    openPickerWithFutureResponse();
    expect(screen.getByText("GPT-6 Preview")).toBeTruthy();
    expect(screen.getByText("Plain Model")).toBeTruthy();
  });

  it("shows the reasoning badge from the block, ignoring unknown sub-fields", () => {
    openPickerWithFutureResponse();
    const badge = dictionaries.en["model.reasoningBadge"];
    expect(screen.getAllByLabelText(badge).length).toBeGreaterThan(0);
  });

  it("treats a missing reasoning block as not-reasoning, not an error", () => {
    const models = FUTURE_RESPONSE.models as ModelEntry[];
    const plain = models.find((m) => m.id === "plain-model")!;
    // The exact expression ChatInput.tsx uses for toggle visibility.
    expect(Boolean(plain.reasoning?.supported)).toBe(false);
    const future = models.find((m) => m.id === "gpt-6-preview")!;
    expect(Boolean(future.reasoning?.supported)).toBe(true);
  });
});
