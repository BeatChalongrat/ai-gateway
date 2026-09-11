// FIX33 — the context meter is QUIET: nothing below 70 % of the budget, a
// muted line from 70 % up, and the compact call carries the user's
// instructions. Spec addendum #1's self-check items.
//
// FIX107 Part A2 — the row says what the system does (a countdown to an
// automatic summary, then "summarized automatically"), the counts live in the
// details, and the manual control is a quiet chevron that opens them instead
// of a bordered button on the row.

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
const fill = (s: string, v: Record<string, string>) => s.replace(/\{(\w+)\}/g, (_, k) => v[k] ?? `{${k}}`);
const openDetails = () => fireEvent.click(screen.getByTestId("context-compact-button"));

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

  it("from 70 %: a countdown to an AUTOMATIC summary; the control is a quiet icon, not a call to action", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.74));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toBe(fill(dictionaries.en["context.meter"], { pct: "74", n: "12" }));
    expect(text).toMatch(/automatically/);
    const control = screen.getByTestId("context-compact-button");
    expect(control.textContent).toBe("");
    expect(control.getAttribute("aria-label")).toBe(dictionaries.en["context.details"]);
    expect(control.getAttribute("aria-expanded")).toBe("false");
    openDetails();
    expect(screen.getByTestId("context-detail").textContent).toBe(fill(dictionaries.en["context.detail.approaching"], { trigger: "85" }));
    expect(screen.getByText(dictionaries.en["context.detail.auto"])).toBeTruthy();
  });

  // FIX102 — once trimming is routine the old meter read "Context 100% used"
  // on every turn. FIX107 — and then it read like a fault ("sees 5 of 24").
  it("trimming routinely: a settled line, no percent, no counts on the row; the counts are in the details", async () => {
    mockApi.getContextInfo.mockResolvedValue(
      info(0.72, { compacted: true, state_present: true, state_injected: true, messages_total: 24, messages_would_send: 7, sent_tokens: 2854, messages_budget: 3944, est_messages_remaining: 0 }),
    );
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    const status = screen.getByRole("status");
    expect(status.textContent).toBe(dictionaries.en["context.label.summarized"]);
    expect(status.textContent).not.toMatch(/\d/);
    expect(status.getAttribute("title")).toBe("2,854 / 3,944 tokens");
    openDetails();
    expect(screen.getByTestId("context-detail").textContent).toBe(fill(dictionaries.en["context.rolling"], { sent: "5", total: "24" }));
    expect(screen.getByText(dictionaries.en["context.detail.auto"])).toBeTruthy();
  });

  it("trimming with no summary (memory off) says so, and does not claim the system handles it", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.3, { compacted: true, state_present: false, messages_total: 30, messages_would_send: 6 }));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByRole("status").textContent).toBe(dictionaries.en["context.label.noSummary"]);
    openDetails();
    expect(screen.getByTestId("context-detail").textContent).toBe(fill(dictionaries.en["context.rolling.noSummary"], { sent: "6", total: "30" }));
    expect(screen.queryByText(dictionaries.en["context.detail.auto"])).toBeNull();
  });

  // FIX105 — the first exchange is no longer kept forever; when it is among
  // what the assistant no longer sees word for word, the details say so.
  it("trimming with the first exchange left out says the start of the conversation is in the summary", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.4, { compacted: true, state_present: true, state_injected: true, head_dropped: true, messages_total: 6, messages_would_send: 3 }));
    const { unmount } = render(<ContextMeter conversationId="c1" />);
    await act(tick);
    openDetails();
    expect(screen.getByTestId("context-detail").textContent).toBe(fill(dictionaries.en["context.rolling.headSummarized"], { sent: "1", total: "6" }));
    unmount();
    mockApi.getContextInfo.mockResolvedValue(info(0.4, { compacted: true, state_present: false, head_dropped: true, messages_total: 6, messages_would_send: 1 }));
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    openDetails();
    expect(screen.getByTestId("context-detail").textContent).toBe(fill(dictionaries.en["context.rolling.headNoSummary"], { sent: "1", total: "6" }));
    for (const k of ["context.rolling.headSummarized", "context.rolling.headNoSummary", "error.message_too_long"] as const) {
      expect(dictionaries.th[k]).toBeTruthy();
    }
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

  it("summarize now, from the details: sends the user's instructions and reports done", async () => {
    mockApi.getContextInfo.mockResolvedValue(info(0.8));
    mockApi.compactConversation.mockResolvedValue({ coverage_reached: true, messages_total: 40 });
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    openDetails();
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
    openDetails();
    fireEvent.click(screen.getByTestId("context-compact-submit"));
    await act(tick);
    expect(mockToast).toHaveBeenCalledWith(dictionaries.en["context.compact.failed"]);
  });

  it("the Thai and English copy exist for every key (both surfaces read the same dict)", () => {
    for (const k of [
      "gen.compacting", "context.meter", "context.meter.full", "context.rolling", "context.rolling.noSummary", "context.compactNow",
      "context.compact.title", "context.compact.done", "context.compact.failed", "context.label.summarized", "context.label.noSummary",
      "context.details", "context.detail.title", "context.detail.auto", "context.detail.approaching",
    ] as const) {
      expect(dictionaries.en[k]).toBeTruthy();
      expect(dictionaries.th[k]).toBeTruthy();
    }
    // The Thai row never tells the user something is wrong while the system is doing its job.
    expect(dictionaries.th["context.label.summarized"]).toContain("อัตโนมัติ");
    expect(dictionaries.th["context.meter"]).toContain("อัตโนมัติ");
  });
});
