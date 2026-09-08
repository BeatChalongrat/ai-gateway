// FIX86 Part D — the model pill stays one line (it wrapped to three at
// 390 px) with the full name reachable, and a disabled pill is visibly
// disabled with the design system's own token.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const LONG = "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0 (self-hosted, reasoning)";
vi.mock("@/stores/modelStore", () => ({
  AUTO_MODEL_ID: "auto",
  useModelStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      models: [{ id: "m1", name: LONG, provider: "sranai", capabilities: [] }],
      selectedModelId: "m1",
      loaded: true,
      loading: false,
      error: null,
      fetchModels: () => Promise.resolve(),
    }),
}));

import { ModelSelector } from "./ModelSelector";

afterEach(cleanup);

describe("model pill (FIX86 Part D)", () => {
  it("truncates to one line and exposes the full name as the title", () => {
    render(<ModelSelector activeModelId="m1" onPick={() => {}} />);
    const label = screen.getByTestId("model-trigger-label");
    expect(label.className).toMatch(/\btruncate\b/);
    expect(label.className).toMatch(/max-w-/);
    const button = label.closest("button")!;
    expect(button.getAttribute("title")).toBe(LONG);
  });

  it("uses the Button token when disabled, not a barely-there opacity", () => {
    render(<ModelSelector activeModelId="m1" onPick={() => {}} disabled />);
    const button = screen.getByTestId("model-trigger-label").closest("button")!;
    expect(button.disabled).toBe(true);
    expect(button.className).toMatch(/disabled:opacity-40/);
    expect(button.className).toMatch(/disabled:cursor-not-allowed/);
    expect(button.className).not.toMatch(/disabled:opacity-80/);
  });
});
