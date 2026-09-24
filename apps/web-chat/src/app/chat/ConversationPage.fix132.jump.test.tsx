// FIX132 F12 — the conversation page turns a jump request (a sidebar /
// Ctrl+K content hit, a #msg-<id> link, "go to the first message") into:
// load down to the message with one `through=` request, then hand the
// thread the message to open at. NIGHT3 J9: the hit opened the chat at
// the bottom, the message ~59 "load older" clicks away.
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, threadProps, stub, toastMock } = vi.hoisted(() => ({
  mockApi: { getContextInfo: vi.fn(), listMessages: vi.fn(), listFiles: vi.fn(), getConversation: vi.fn() },
  threadProps: { current: null as null | Record<string, unknown> },
  stub: (_name: string) => () => null,
  toastMock: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/lib/hostApi", () => ({ hostApi: { contextInfo: vi.fn(), compactContext: vi.fn() } }));
vi.mock("@/components/ui/Toast", () => ({ toast: toastMock }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ conversationId: "c1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/c1",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/chat/AttachmentDropZone", () => ({ AttachmentDropZone: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/chat/ChatInput", () => ({ ChatInput: stub("composer"), uploadFiles: vi.fn() }));
vi.mock("@/components/chat/ChatThread", () => ({
  ChatThread: (p: Record<string, unknown>) => {
    threadProps.current = p;
    return null;
  },
}));
vi.mock("@/components/chat/ContextMeter", () => ({ ContextMeter: stub("meter") }));
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
import { requestMessageJump, useMessageJumpStore } from "@/lib/messageJump";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

const initial = useChatStore.getState();
const m = (i: number): Message => ({ id: `m${i}`, role: i % 2 ? "assistant" : "user", content: `#${i}`, status: "completed", created_at: new Date(Date.UTC(2026, 8, 1, 0, 0, i)).toISOString() }) as Message;
const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, k) => m(a + k));
const settle = async () => {
  for (let i = 0; i < 6; i++) await act(() => new Promise<void>((r) => setTimeout(r, 0)));
};
const jumpProp = () => threadProps.current?.jumpTo as { messageId: string; nonce: number } | null | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  threadProps.current = null;
  useMessageJumpStore.setState({ request: null });
  useChatStore.setState({ ...initial, conversations: [{ id: "c1", title: "t", model_id: "m", message_count: 3000, created_at: "", updated_at: "" }], messages: { c1: range(2950, 2999) }, messagesLoaded: { c1: true }, hasMoreMessages: { c1: true } }, true);
  mockApi.getContextInfo.mockResolvedValue(null);
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("FIX132 F12 — the page opens the chat at the message", () => {
  it("a #msg-<id> link to a message not loaded yet: one through= request, then the thread jumps", async () => {
    window.history.replaceState(null, "", "/chat/c1#msg-m16");
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(16, 2949), has_more: true });
    render(<ConversationPage />);
    await settle();
    expect(mockApi.listMessages).toHaveBeenCalledWith("c1", { limit: 1000, before: "m2950", through: "m16" });
    expect(jumpProp()).toMatchObject({ messageId: "m16" });
  });

  it("a search hit clicked while this chat is open jumps too (no new page, no hashchange)", async () => {
    render(<ConversationPage />);
    await settle();
    expect(jumpProp() ?? null).toBeNull();
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(100, 2949), has_more: true });
    act(() => requestMessageJump("c1", "m100"));
    await settle();
    expect(jumpProp()).toMatchObject({ messageId: "m100" });
  });

  it("'go to the first message' loads to the start and jumps to it", async () => {
    mockApi.listMessages
      .mockResolvedValueOnce({ messages: range(1950, 2949), has_more: true })
      .mockResolvedValueOnce({ messages: range(950, 1949), has_more: true })
      .mockResolvedValueOnce({ messages: range(0, 949), has_more: false });
    render(<ConversationPage />);
    await settle();
    const onJumpToFirst = threadProps.current?.onJumpToFirst as () => void;
    expect(typeof onJumpToFirst).toBe("function");
    act(() => onJumpToFirst());
    await settle();
    expect(jumpProp()).toMatchObject({ messageId: "m0" });
  });

  it("a hit that is gone says so instead of silently opening at the bottom", async () => {
    mockApi.listMessages.mockResolvedValueOnce({ messages: range(0, 2949), has_more: false });
    render(<ConversationPage />);
    await settle();
    act(() => requestMessageJump("c1", "deleted-msg"));
    await settle();
    expect(jumpProp() ?? null).toBeNull();
    expect(toastMock).toHaveBeenCalled();
  });
});
