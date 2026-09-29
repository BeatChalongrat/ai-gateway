// AM5 phase B — the model control is the chat's model control.
//
// AM5 also gave the agent's menu rows of its own (one row per model, a line
// about the single in-house model, "เลือกโมเดลเอง" to reach the chat's
// picker). AM6 cancelled those rows: the menu is the chat's picker and
// nothing else, and am6_picker.test.tsx holds what it must do. What AM5 said
// about the pill and the shell still holds, and is tested here.
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

import type { AgentModels } from "../types";
import { ModelControl } from "./ModelControl";

const agentModels = (p: Partial<AgentModels> = {}): AgentModels => ({
  uses: "vision",
  choice: "auto",
  auto_model: "qwen3.8-27b-fp8",
  models: [
    { id: "qwen3.8-27b-fp8", allowed: true },
    { id: "typhoon-ocr", allowed: true },
    { id: "gpt-4o", allowed: false },
  ],
  blocked_reason: "องค์กรไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร โมเดลนอกองค์กรจึงเลือกไม่ได้",
  hidden_reason: "agent นี้ต้องใช้โมเดลที่อ่านภาพได้",
  reads: "",
  note: "",
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

function mount(p: AgentModels) {
  return render(<ModelControl installId="i-1" models={p} onModels={vi.fn()} />);
}

describe("AM5 — the control is the chat's model control", () => {
  it("the trigger is the same element with the same classes", () => {
    const chat = render(<ModelSelector activeModelId="qwen3.8-27b-fp8" compact />);
    const chatTrigger = chat.getByTestId("model-trigger-label").closest("button") as HTMLElement;
    const want = { trigger: chatTrigger.className, label: chat.getByTestId("model-trigger-label").className };
    fireEvent.click(chatTrigger);
    const wantMenu = (chat.getByRole("listbox") as HTMLElement).className.replace(/\s+/g, " ").trim();
    chat.unmount();

    mount(agentModels());
    const trigger = screen.getByTestId("am-model-control");
    expect({ trigger: trigger.className, label: within(trigger).getByTestId("model-trigger-label").className }).toEqual(want);
    fireEvent.click(trigger);
    expect((screen.getByTestId("am-model-menu") as HTMLElement).className.replace(/\s+/g, " ").trim()).toBe(wantMenu);
  });

  it("the pill names the agent's model, as the chat's names the conversation's", () => {
    const chat = render(<ModelSelector activeModelId="typhoon-ocr" compact />);
    const want = chat.getByTestId("model-trigger-label").closest("button")!.outerHTML;
    chat.unmount();

    mount(agentModels({ choice: "typhoon-ocr" }));
    const trigger = screen.getByTestId("am-model-control");
    expect(trigger.textContent).toBe("typhoon-ocr");
    // the same pill; the agent's says what the model is for, and can be found by a test
    expect(trigger.outerHTML.replace(' aria-label="โมเดลที่อ่านเอกสาร: typhoon-ocr"', "").replace(' data-testid="am-model-control"', "")).toBe(want);
  });

  it("reads Thai in an English app", () => {
    mount(agentModels());
    expect(screen.getByTestId("am-model-control").textContent).toBe("อัตโนมัติ");
    fireEvent.click(screen.getByTestId("am-model-control"));
    expect(screen.getByPlaceholderText("ค้นหาโมเดล…")).toBeTruthy();
    expect(screen.getByTestId("am-model-menu").textContent).not.toContain("Auto");
  });
});
