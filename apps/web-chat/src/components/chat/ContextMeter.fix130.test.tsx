// FIX130 C3.5 — a phone user is told old messages are no longer sent.
//
// NIGHT2 F7: at 360 px the long-chat line measured 0 × 0 — it lives in the
// conversation header, which is `hidden md:flex`, and the phone's top bar had
// nothing in its place; 32 of 40 messages were silently not sent. F10: on a
// desktop it was 11-px grey text that did nothing and never said how many.
//
// Pinned here:
//   - the compact chip (phone) says "ส่งให้ผู้ช่วย 8 จาก 40 ข้อความ" and opens the
//     details on tap; it reads the numbers without asking for them again;
//   - the header row is clickable and carries the count;
//   - on the real conversation page the chip sits OUTSIDE anything hidden
//     below md (a DOM assertion on the rendered page: jsdom applies no CSS,
//     so the class chain is what decides whether a phone ever paints it).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, stub } = vi.hoisted(() => ({
  mockApi: { getContextInfo: vi.fn(), compactConversation: vi.fn(), listMessages: vi.fn(), listFiles: vi.fn(), getConversation: vi.fn() },
  stub: (_name: string) => () => null,
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/lib/hostApi", () => ({ hostApi: { contextInfo: vi.fn(), compactContext: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ conversationId: "c1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/c1",
  useSearchParams: () => new URLSearchParams(),
}));
// The page's heavy children are stubbed; ContextMeter is the real one.
vi.mock("@/components/chat/AttachmentDropZone", () => ({ AttachmentDropZone: ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={className}>{children}</div> }));
vi.mock("@/components/chat/ChatInput", () => ({ ChatInput: stub("composer"), uploadFiles: vi.fn() }));
vi.mock("@/components/chat/ChatThread", () => ({ ChatThread: stub("thread") }));
vi.mock("@/components/chat/ConversationFiles", () => ({ ConversationFiles: stub("files") }));
vi.mock("@/components/chat/EmptyState", () => ({ EmptyState: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/chat/ExportMenu", () => ({ ExportMenu: stub("export") }));
vi.mock("@/components/chat/MemoryIndicator", () => ({ MemoryIndicator: stub("memory") }));
vi.mock("@/components/chat/MessageQueue", () => ({ MessageQueue: stub("queue") }));
vi.mock("@/components/chat/TemporaryChatBanner", () => ({ TemporaryChatBanner: stub("temp") }));
vi.mock("@/components/chat/ShareModal", () => ({ ShareModal: stub("share") }));
vi.mock("@/components/chat/FilePreviewModal", () => ({ FilePreviewPane: stub("preview") }));
vi.mock("@/components/canvas/CanvasPanel", () => ({ CanvasPanel: stub("canvas") }));
vi.mock("@/components/workspace/WorkspaceButton", () => ({ WorkspaceButton: stub("wsbtn") }));
vi.mock("@/components/workspace/WorkspacePanel", () => ({ WorkspacePanel: stub("wspanel") }));

import ConversationPage from "@/app/chat/[conversationId]/page";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextInfo, Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { ContextMeter } from "./ContextMeter";

const initial = useChatStore.getState();
const th = dictionaries.th;
const fill = (s: string, v: Record<string, string>) => s.replace(/\{(\w+)\}/g, (_, k) => v[k] ?? `{${k}}`);
// 40 messages in the chat, 8 sent this turn, no summary (memory off) — NIGHT2 J10.
const trimming: ContextInfo = {
  used_ratio: 0.4, history_tokens: 4000, messages_budget: 10_000, threshold_tokens: 8500,
  messages_total: 40, messages_would_send: 8, state_present: false, compacted: true, est_messages_remaining: 0,
} as ContextInfo;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  const msgs = [
    { id: "u1", role: "user", status: "completed", content: "q", created_at: "2026-09-23T00:00:00Z" },
    { id: "a1", role: "assistant", status: "completed", content: "a", created_at: "2026-09-23T00:00:01Z" },
  ] as Message[];
  useChatStore.setState({ ...initial, messages: { c1: msgs }, messagesLoaded: { c1: true }, contextBudgetByConv: { c1: trimming } }, true);
  mockApi.getContextInfo.mockResolvedValue(trimming);
  mockApi.listMessages.mockResolvedValue({ messages: msgs, has_more: false });
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX130 C3.5 — the phone chip", () => {
  it("says how many messages are sent, opens the details on tap, and does not fetch again", async () => {
    render(<ContextMeter conversationId="c1" variant="compact" passive />);
    await act(tick);
    const chip = screen.getByTestId("context-meter-compact");
    expect(chip).toHaveTextContent(fill(th["context.chip.sent"], { sent: "8", total: "40" }));
    expect(chip).toHaveTextContent("ส่งให้ผู้ช่วย 8 จาก 40 ข้อความ");
    expect(mockApi.getContextInfo).not.toHaveBeenCalled();
    fireEvent.click(chip.querySelector("button")!);
    expect(screen.getByTestId("context-detail")).toHaveTextContent(fill(th["context.rolling.noSummary"], { sent: "8", total: "40" }));
  });
});

describe("FIX130 C3.5 — the desktop row", () => {
  it("carries the count and is itself clickable", async () => {
    render(<ContextMeter conversationId="c1" />);
    await act(tick);
    expect(screen.getByRole("status")).toHaveTextContent("ส่งให้ผู้ช่วย 8 จาก 40 ข้อความ");
    fireEvent.click(screen.getByTestId("context-meter-label"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("FIX130 C3.5 — on the conversation page, a phone can see it", () => {
  const hiddenBelowMd = (el: Element | null): boolean => {
    for (let n = el; n; n = n.parentElement) {
      const cls = ` ${n.getAttribute("class") ?? ""} `;
      if (cls.includes(" hidden ")) return true; // `hidden md:flex` = not painted below 768 px
    }
    return false;
  };

  it("the compact chip is rendered outside every below-md hidden container; the header row stays desktop-only", async () => {
    render(<ConversationPage />);
    await act(tick);
    const chip = screen.getByTestId("context-meter-compact");
    expect(chip).toHaveTextContent("ส่งให้ผู้ช่วย 8 จาก 40 ข้อความ");
    expect(hiddenBelowMd(chip)).toBe(false);
    // …and it is hidden from md up, where the header row shows instead.
    expect(chip.closest(".md\\:hidden")).not.toBeNull();
    expect(hiddenBelowMd(screen.getByTestId("context-meter"))).toBe(true);
  });
});
