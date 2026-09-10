// FIX33 — the context meter is QUIET: nothing below 70 % of the budget, a
// muted line + "Summarize and continue" from 70 % up, and the compact call
// carries the user's instructions. Spec addendum #1's self-check items.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockToast } = vi.hoisted(() => ({
  mockApi: { getContextInfo: vi.fn(), compactConversation: vi.fn() },
  mockToast: vi.fn(),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/lib/hostApi", () => ({ hostApi: { contextInfo: vi.fn(), compactContext: vi.fn() } }));
vi.mock("@/components/ui/Toast", () => ({ toast: mockToast }));

import { CONTEXT_METER_SHOW_RATIO, shouldShowMeter } from "@/lib/compaction";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextInfo } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { ContextMeter } from "./ContextMeter";

const initialState = useChatStore.getState();
const info = (used_ratio: number, extra: Partial<ContextInfo> = {}): ContextInfo => ({
  used_ratio,
  history_tokens: Math.round(used_ratio * 100_000),
  messages_budget: 100_000,
  threshold_tokens: 85_000,
  messages_total: 40,
  state_present: false,
  compacted: false,
  est_messages_remaining: 12,
  ...extra,
});
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

beforeEach(() => {
  useChatStore.setState({ ...initialState, messages: { c1: [] }, contextBudgetByConv: {} }, true);
  mockApi.getContextInfo.mockReset();
  mockApi.compactConversation.mockReset();
  mockToast.mockReset();
});
afterEach(cleanup);

describe("shouldShowMeter", () => {
  it("hides below the show ratio, shows from it, honours the server's show_meter", () => {
    expect(CONTEXT_METER_SHOW_RATIO).toBe(0.7);
    expect(shouldShowMeter(null)).toBe(false);
    expect(shouldShowMeter(info(0.69))).toBe(false);
    expect(shouldShowMeter(info(0.7))).toBe(true);
    expect(shouldShowMeter(info(0.9, { show_meter: false }))).toBe(false);
    expect(shouldShowMeter(info(0.2, { show_meter: true }))).toBe(true);
    // FIX102 — trimming is always said, whatever the ratio of what is sent.
    expect(shouldShowMeter(info(0.6, { compacted: true }))).toBe(true);
    expect(shouldShowMeter(info(0.1, { omitted_older: 40 }))).toBe(true);
  });
});

describe("ContextMeter", () => {
  it("renders NOTHING when the conversation is below 70 % — no number, no bar, no button", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.5));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(mockApi.getContextInfo).toHaveBeenCalledWith("c1");
    expect(screen.queryByTestId("context-meter")).toBeNull();
    expect(screen.queryByTestId("context-compact-button")).toBeNull();
  });

  it("from 70 %: a muted line with percent + messages left, and the compact button appears WITH it", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.74));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByTestId("context-meter")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("74%");
    expect(screen.getByRole("status").textContent).toContain("12");
    expect(screen.getByTestId("context-compact-button").textContent).toBe(dictionaries.en["context.compactNow"]);
  });

  // FIX102 — once trimming is routine the old meter read "Context 100% used"
  // on every turn (the probe measured 340 % underneath). It now says what is
  // sent, whatever the ratio, and never shows a percent in that state.
  it("trimming routinely: says what the assistant sees, no percent, even below 70 %", async () => {
    mockApi.getContextInfo.mockResolvedValue(
      info(0.72, { compacted: true, state_present: true, state_injected: true, messages_total: 24, messages_would_send: 7, sent_tokens: 2854, messages_budget: 3944, est_messages_remaining: 0 }),
    );
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toBe(dictionaries.en["context.rolling"].replace("{sent}", "5").replace("{total}", "24"));
    expect(text).not.toContain("%");
    expect(screen.getByRole("status").getAttribute("title")).toBe("2,854 / 3,944 tokens");
    expect(screen.getByTestId("context-compact-button")).toBeTruthy();
  });

  it("trimming with no summary (memory off) says the rest are not summarized", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.3, { compacted: true, state_present: false, messages_total: 30, messages_would_send: 6 }));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByRole("status").textContent).toBe(dictionaries.en["context.rolling.noSummary"].replace("{sent}", "6").replace("{total}", "30"));
  });

  it("an older desktop build that still sends the LOADED ratio (2.29) never reads above 100 %", async () => {
    // Not trimming yet but over: capped. Trimming: no percent at all.
    mockApi.getContextInfo.mockResolvedValue(info(2.29, { est_messages_remaining: 0 }));
    const { unmount } = render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByRole("status").textContent).toContain("100%");
    unmount();
    mockApi.getContextInfo.mockResolvedValue(info(2.29, { compacted: true, state_present: true, messages_sent: 5, messages_total: 24 }));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByRole("status").textContent).not.toMatch(/\d{3}%/);
  });

  it("'Summarize and continue' sends the user's instructions and reports done", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.8));
    mockApi.compactConversation.mockResolvedValue({ coverage_reached: true, messages_total: 40 });
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    fireEvent.click(screen.getByTestId("context-compact-button"));
    const box = screen.getByPlaceholderText(dictionaries.en["context.compact.placeholder"]);
    fireEvent.change(box, { target: { value: "keep the code" } });
    fireEvent.click(screen.getByTestId("context-compact-submit"));
    await act(tick);
    expect(mockApi.compactConversation).toHaveBeenCalledWith("c1", "keep the code");
    expect(mockToast).toHaveBeenCalledWith(dictionaries.en["context.compact.done"]);
    expect(mockApi.getContextInfo).toHaveBeenCalledTimes(2); // refreshed after
  });

  it("a failed summary says nothing changed", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.8));
    mockApi.compactConversation.mockRejectedValue(new Error("boom"));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    fireEvent.click(screen.getByTestId("context-compact-button"));
    fireEvent.click(screen.getByTestId("context-compact-submit"));
    await act(tick);
    expect(mockToast).toHaveBeenCalledWith(dictionaries.en["context.compact.failed"]);
  });

  it("the Thai and English copy exist for every key (both surfaces read the same dict)", () => {
    for (const k of ["gen.compacting", "context.meter", "context.meter.full", "context.rolling", "context.rolling.noSummary", "context.compactNow", "context.compact.title", "context.compact.done", "context.compact.failed"] as const) {
      expect(dictionaries.en[k]).toBeTruthy();
      expect(dictionaries.th[k]).toBeTruthy();
    }
  });
});
