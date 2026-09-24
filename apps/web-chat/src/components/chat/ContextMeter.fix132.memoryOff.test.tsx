// FIX132 N3 — "สรุปตอนนี้" was offered in memory-off (and temporary) chats,
// where it can only fail: NIGHT3 j10 pressed สรุป, POST /compact answered 400
// in 1 ms, the reason ("แชทนี้ปิดความจำอยู่ จึงไม่มีที่เก็บบทสรุป") was a ~2 s
// toast, and the popover stayed open, unchanged. Now the action is disabled
// with that sentence inline, and nothing is sent.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockToast } = vi.hoisted(() => ({
  mockApi: { getContextInfo: vi.fn(), compactConversation: vi.fn() },
  mockToast: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/lib/hostApi", () => ({ hostApi: { contextInfo: vi.fn(), compactContext: vi.fn() } }));
vi.mock("@/components/ui/Toast", () => ({ toast: mockToast, toastError: mockToast }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextInfo, Conversation } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { ContextMeter } from "./ContextMeter";

const th = dictionaries.th;
const initial = useChatStore.getState();
const trimming = { used_ratio: 0.4, history_tokens: 4000, messages_budget: 10_000, threshold_tokens: 8500, messages_total: 42, messages_would_send: 9, state_present: false, compacted: true, est_messages_remaining: 0 } as ContextInfo;
const conv = (over: Partial<Conversation>) => ({ id: "c1", title: "t", model_id: "m", message_count: 42, created_at: "", updated_at: "", ...over }) as Conversation;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  mockApi.getContextInfo.mockResolvedValue(trimming);
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

async function openFor(c: Conversation) {
  useChatStore.setState({ ...initial, conversations: [c], messages: { c1: [] }, contextBudgetByConv: { c1: trimming } }, true);
  render(<ContextMeter conversationId="c1" />);
  await act(tick);
  fireEvent.click(screen.getByTestId("context-compact-button"));
}

describe("FIX132 N3 — no summarise button that can only fail", () => {
  for (const [name, c] of [
    ["memory off", conv({ memory_enabled: false })],
    ["temporary chat", conv({ memory_enabled: false, is_temporary: true })],
  ] as const) {
    it(`${name}: the summarise action is disabled and says why, inline; nothing is sent`, async () => {
      await openFor(c);
      const submit = screen.getByTestId("context-compact-submit");
      expect(submit).toBeDisabled();
      const reason = screen.getByTestId("context-compact-unavailable");
      expect(reason).toHaveTextContent(th["context.compact.memoryOff"]);
      expect(submit.getAttribute("aria-describedby")).toBe(reason.id);
      expect(screen.queryByPlaceholderText(th["context.compact.placeholder"])).toBeNull();
      fireEvent.click(submit);
      await act(tick);
      expect(mockApi.compactConversation).not.toHaveBeenCalled();
    });
  }

  it("memory on: the action is there as before", async () => {
    await openFor(conv({ memory_enabled: true }));
    expect(screen.getByTestId("context-compact-submit")).not.toBeDisabled();
    expect(screen.queryByTestId("context-compact-unavailable")).toBeNull();
  });
});
