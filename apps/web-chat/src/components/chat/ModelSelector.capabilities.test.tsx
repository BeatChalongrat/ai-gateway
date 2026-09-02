// FIX47 Part C — the picker offers what an admin chose and refuses,
// with a reason, what the message being composed cannot use.
//
//  * show_in_chat=false rows are not offered (the one the conversation
//    already uses still is, so the pill can name it);
//  * with an image attached, a model that cannot read images is disabled
//    and says why — not pickable-then-broken;
//  * a model that cannot use tools says so under its name.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ModelEntry } from "@/lib/types";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ModelSelector } from "./ModelSelector";

function model(extra?: Partial<ModelEntry>): ModelEntry {
  return {
    id: "gpt-4o",
    name: "GPT-4o",
    provider: "openai",
    description: "",
    context_window: 128000,
    capabilities: ["vision", "function_calling"],
    icon: "",
    category: "flagship",
    allowed: true,
    ...extra,
  };
}

const CATALOG: ModelEntry[] = [
  model(),
  model({ id: "deepseek-r1", name: "DeepSeek R1", provider: "openrouter", capabilities: ["reasoning"] }),
  model({ id: "hidden-one", name: "Hidden One", provider: "openai", show_in_chat: false }),
];

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

function open(props: { activeModelId?: string; needsVision?: boolean } = {}, onPick = vi.fn()) {
  useModelStore.setState({ models: CATALOG, loaded: true, loading: false });
  render(<ModelSelector activeModelId={props.activeModelId ?? AUTO_MODEL_ID} needsVision={props.needsVision} onPick={onPick} />);
  fireEvent.click(screen.getByRole("button"));
  return onPick;
}

const rowNames = () => screen.getAllByTestId("model-row-name").map((n) => n.textContent);

describe("ModelSelector — show_in_chat", () => {
  it("does not offer a model the admin hid", () => {
    open();
    expect(rowNames()).toContain("GPT-4o");
    expect(rowNames()).not.toContain("Hidden One");
  });

  it("still lists the hidden model this conversation already uses", () => {
    open({ activeModelId: "hidden-one" });
    expect(rowNames()).toContain("Hidden One");
  });

  it("an entry without the field (older chat-edge) is offered", () => {
    expect(CATALOG[0].show_in_chat).toBeUndefined();
    open();
    expect(rowNames()).toContain("GPT-4o");
  });
});

describe("ModelSelector — what the message needs", () => {
  it("with an image attached, a model that cannot read images is disabled with the reason", () => {
    const onPick = open({ needsVision: true });
    const disabled = screen.getAllByTestId("model-row-disabled");
    expect(disabled.length).toBe(1);
    expect(disabled[0].getAttribute("aria-disabled")).toBe("true");
    expect(screen.getAllByTestId("model-row-reason")[0].textContent).toBe(dictionaries.en["model.cantReadImages"]);
    fireEvent.click(disabled[0]);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("without an image every chat model is pickable", () => {
    open({ needsVision: false });
    expect(screen.queryByTestId("model-row-disabled")).toBeNull();
  });

  // FIX47.6 Part C — the sub-line for a model without tool support is
  // guidance ("great for general Q&A"), never the defect-flavoured
  // "no tools" it replaced.
  it("a model that cannot use tools gets the guidance line, not a defect label", () => {
    open();
    expect(screen.getAllByText(new RegExp(dictionaries.en["model.subGeneralQA"])).length).toBeGreaterThan(0);
    expect(screen.queryByText(/no tools/)).toBeNull();
  });

  it("Thai reasons exist", () => {
    expect(dictionaries.th["model.cantReadImages"]).toBeTruthy();
    expect(dictionaries.th["model.subGeneralQA"]).toBeTruthy();
  });
});
