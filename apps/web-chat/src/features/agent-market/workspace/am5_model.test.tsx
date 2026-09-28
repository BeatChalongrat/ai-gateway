// AM5 phase B — the model menu.
//
// On the owner's desktop the menu offered "อัตโนมัติ (แนะนำ) · qwen3.8-27b-fp8 ·
// ≈0.02 บาท · 21 วินาที", a locked "เร็วและประหยัด", and "แม่นที่สุด ·
// qwen3.8-27b-fp8 · ≈0.02 บาท · 21 วินาที": the same model twice, and the only
// different row disabled. The menu never shows two rows that resolve to the
// same model, says so in one line when the organisation has one model of its
// own, and "เลือกโมเดลเอง" opens the chat's own picker (search, provider
// groups) with outside models locked and the reason on the row.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { ModelSelector } from "@/components/chat/ModelSelector";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import type { ModelOption, ModelPicker } from "../types";
import { collapseOptions, ModelControl } from "./ModelControl";

const REASON = "โมเดลนี้อยู่นอกองค์กร และองค์กรยังไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร — ผู้ดูแลเปิดได้ในหน้าผู้ดูแล";

const opt = (o: Partial<ModelOption> & { id: string; model: string; label: string }): ModelOption => ({
  outcome: "own",
  self_hosted: true,
  cost_thb: 0.02,
  seconds: 21,
  disabled: false,
  ...o,
});

// what the live server sent before AM5 (the owner's screen)
const LIVE_BEFORE: ModelOption[] = [
  opt({ id: "auto", outcome: "auto", label: "อัตโนมัติ (แนะนำ)", model: "qwen3.8-27b-fp8" }),
  opt({ id: "deepseek-v4-flash", outcome: "fast", label: "เร็วและประหยัด", model: "deepseek-v4-flash", self_hosted: false, cost_thb: null, seconds: null, disabled: true, reason: REASON }),
  opt({ id: "qwen3.8-27b-fp8", outcome: "accurate", label: "แม่นที่สุด", model: "qwen3.8-27b-fp8" }),
];

const picker = (p: Partial<ModelPicker> = {}): ModelPicker => ({
  uses: "vision",
  reads: "",
  choice: "auto",
  options: LIVE_BEFORE.slice(0, 2),
  documents_may_leave: false,
  note: "",
  single_in_house: true,
  notice: "ตอนนี้องค์กรมีโมเดลในองค์กรตัวเดียว",
  locked_reason: "agent ยังใช้โมเดลนี้ไม่ได้ เลือกจากโมเดลที่ไม่ได้ล็อกไว้",
  custom: [
    { id: "deepseek-v4-flash", self_hosted: false, vision: false, allowed: false, reason: REASON },
    { id: "gpt-4o", self_hosted: false, vision: true, allowed: false, reason: REASON },
    { id: "qwen3.8-27b-fp8", self_hosted: true, vision: true, allowed: true },
    { id: "typhoon-ocr", self_hosted: true, vision: true, allowed: true },
  ],
  ...p,
});

const chatModel = (id: string, o: Partial<ModelEntry> = {}): ModelEntry => ({
  id,
  name: id,
  provider: "own",
  description: "",
  context_window: 32000,
  capabilities: ["vision", "function_calling"],
  icon: "",
  category: "fast",
  allowed: true,
  ...o,
});

beforeEach(() => {
  useI18nStore.setState({ locale: "en" }); // the owner's app runs in English; the workspace is Thai
  useModelStore.setState({
    loaded: true,
    loading: false,
    loadFailed: false,
    models: [
      chatModel("qwen3.8-27b-fp8", { self_hosted: true }),
      chatModel("typhoon-ocr", { self_hosted: true }),
      chatModel("gpt-4o", { provider: "openai" }),
      chatModel("deepseek-v4-flash", { provider: "deepseek", capabilities: ["function_calling"] }),
      chatModel("sora-video", { provider: "openai", model_kind: "video" }),
    ],
  } as never);
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
  window.localStorage.clear();
});
afterEach(cleanup);

function mount(p: ModelPicker, roundChoice: string | null = null) {
  const onRoundChoice = vi.fn();
  const view = render(<ModelControl installId="i-1" picker={p} onPicker={vi.fn()} roundChoice={roundChoice} onRoundChoice={onRoundChoice} />);
  return { view, onRoundChoice };
}

describe("AM5 — never two rows that resolve to the same model", () => {
  it("collapses what an older server still sends: the row that repeats อัตโนมัติ goes", () => {
    const rows = collapseOptions(LIVE_BEFORE);
    expect(rows.map((o) => o.model)).toEqual(["qwen3.8-27b-fp8", "deepseek-v4-flash"]);
    expect(rows.map((o) => o.label)).toEqual(["อัตโนมัติ (แนะนำ)", "เร็วและประหยัด"]);
  });

  it("the menu on the owner's screen: two rows, the outside one locked with its reason, and one line about the single model", () => {
    mount(picker({ options: LIVE_BEFORE }));
    fireEvent.click(screen.getByTestId("am-model-control"));
    const rows = screen.getAllByTestId("am-model-option");
    expect(rows.map((r) => r.getAttribute("data-model"))).toEqual(["qwen3.8-27b-fp8", "deepseek-v4-flash"]);
    expect(rows[0]!.textContent).toContain("อัตโนมัติ (แนะนำ)");
    expect(rows[0]!.textContent).toContain("qwen3.8-27b-fp8 · ในองค์กร · ≈0.02 บาท · 21 วินาที");
    expect(rows[1]!.textContent).toContain(REASON);
    expect(within(rows[1]!).getByTestId("model-row-disabled")).toBeTruthy();
    expect(screen.getByTestId("am-model-single").textContent).toBe("ตอนนี้องค์กรมีโมเดลในองค์กรตัวเดียว");
  });

  it("in-house models that differ are rows of their own, and there is no single-model line", () => {
    mount(
      picker({
        single_in_house: false,
        notice: "",
        options: [LIVE_BEFORE[0]!, LIVE_BEFORE[1]!, opt({ id: "typhoon-ocr", model: "typhoon-ocr", label: "typhoon-ocr", cost_thb: 0.01, seconds: 12 })],
      }),
    );
    fireEvent.click(screen.getByTestId("am-model-control"));
    expect(screen.getAllByTestId("am-model-option").map((r) => r.getAttribute("data-model"))).toEqual(["qwen3.8-27b-fp8", "deepseek-v4-flash", "typhoon-ocr"]);
    expect(screen.queryByTestId("am-model-single")).toBeNull();
  });

  it("a locked row cannot be chosen; an open one is this round's model", () => {
    const { onRoundChoice } = mount(picker({ single_in_house: false, notice: "", options: [LIVE_BEFORE[0]!, LIVE_BEFORE[1]!, opt({ id: "typhoon-ocr", model: "typhoon-ocr", label: "typhoon-ocr" })] }));
    fireEvent.click(screen.getByTestId("am-model-control"));
    fireEvent.click(within(screen.getAllByTestId("am-model-option")[1]!).getByRole("option"));
    expect(onRoundChoice).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getAllByTestId("am-model-option")[2]!).getByRole("option"));
    expect(onRoundChoice).toHaveBeenCalledWith("typhoon-ocr");
  });
});

describe("AM5 — เลือกโมเดลเอง is the chat's picker", () => {
  it("opens the picker with search and provider groups, Thai in an English app", () => {
    mount(picker());
    fireEvent.click(screen.getByTestId("am-model-control"));
    fireEvent.click(screen.getByTestId("am-model-custom"));
    expect(screen.getByPlaceholderText("ค้นหาโมเดล…")).toBeTruthy();
    expect(screen.getByTestId("model-group-own")).toBeTruthy();
    expect(screen.getByTestId("model-group-full")).toBeTruthy();
    // an agent reads documents: no video, audio or voice models, and no second "Auto"
    expect(screen.queryByText("sora-video")).toBeNull();
    expect(screen.getByTestId("am-model-menu").textContent).not.toContain("Auto");
  });

  it("outside models stay locked with the reason on the row; an in-house one is picked for this round", () => {
    const { onRoundChoice } = mount(picker());
    fireEvent.click(screen.getByTestId("am-model-control"));
    fireEvent.click(screen.getByTestId("am-model-custom"));
    const locked = screen.getAllByTestId("model-row-disabled");
    expect(locked.map((r) => within(r).getByTestId("model-row-name").textContent).sort()).toEqual(["deepseek-v4-flash", "gpt-4o"]);
    for (const r of locked) expect(within(r).getByTestId("model-row-reason").textContent).toBe(REASON);
    fireEvent.click(locked[0]!);
    expect(onRoundChoice).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("typhoon-ocr"));
    expect(onRoundChoice).toHaveBeenCalledWith("typhoon-ocr");
    expect(screen.queryByTestId("am-model-menu")).toBeNull();
  });

  it("the search finds a model by name", () => {
    mount(picker());
    fireEvent.click(screen.getByTestId("am-model-control"));
    fireEvent.click(screen.getByTestId("am-model-custom"));
    fireEvent.change(screen.getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "typhoon" } });
    expect(screen.getAllByTestId("model-row-name").map((n) => n.textContent)).toEqual(["typhoon-ocr"]);
  });

  it("a model chosen by hand for this round is what the control says", () => {
    mount(picker(), "typhoon-ocr");
    expect(screen.getByTestId("am-model-control").textContent).toContain("typhoon-ocr");
  });
});

describe("AM5 — the control is the chat's model control", () => {
  it("the trigger is the same element with the same classes", () => {
    const chat = render(<ModelSelector activeModelId="qwen3.8-27b-fp8" compact />);
    const chatTrigger = chat.getByTestId("model-trigger-label").closest("button") as HTMLElement;
    const want = { trigger: chatTrigger.className, label: chat.getByTestId("model-trigger-label").className };
    fireEvent.click(chatTrigger);
    const wantMenu = (chat.getByRole("listbox") as HTMLElement).className.replace(/\s+/g, " ").trim();
    chat.unmount();

    mount(picker());
    const trigger = screen.getByTestId("am-model-control");
    expect({ trigger: trigger.className, label: within(trigger).getByTestId("model-trigger-label").className }).toEqual(want);
    fireEvent.click(trigger);
    expect((screen.getByTestId("am-model-menu") as HTMLElement).className.replace(/\s+/g, " ").trim()).toBe(wantMenu);
  });
});
