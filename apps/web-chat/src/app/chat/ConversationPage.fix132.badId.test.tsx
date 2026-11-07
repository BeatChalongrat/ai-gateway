// FIX132 F17 — /chat/undefined (or any id that is not a conversation id)
// showed the welcome screen and a composer; sending then ended in a red
// card reading "invalid conversation id", an English toast and a
// regenerate button that could never work (NIGHT3 j5-02). Now the page
// says in Thai that there is no such chat and offers a new one; nothing is
// fetched and there is nothing to send into.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, threadProps, stub, toastMock, params, push } = vi.hoisted(() => ({
  mockApi: { getContextInfo: vi.fn(), listMessages: vi.fn(), listFiles: vi.fn(), getConversation: vi.fn() },
  threadProps: { current: null as null | Record<string, unknown> },
  params: { conversationId: "undefined" },
  push: vi.fn(),
  stub: (_name: string) => () => null,
  toastMock: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/lib/hostApi", () => ({ hostApi: { contextInfo: vi.fn(), compactContext: vi.fn() } }));
vi.mock("@/components/ui/Toast", () => ({ toast: toastMock }));
vi.mock("next/navigation", () => ({
  useParams: () => params,
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => `/chat/${params.conversationId}`,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/chat/AttachmentDropZone", () => ({ AttachmentDropZone: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/chat/ChatInput", () => ({ ChatInput: () => <div data-testid="composer" />, uploadFiles: vi.fn() }));
vi.mock("@/components/chat/ChatThread", () => ({
  ChatThread: (p: Record<string, unknown>) => {
    threadProps.current = p;
    return null;
  },
}));
vi.mock("@/components/chat/ContextMeter", () => ({ ContextMeter: stub("meter") }));
vi.mock("@/components/chat/ConversationFiles", () => ({ ConversationFiles: stub("files") }));
vi.mock("@/components/chat/EmptyState", () => ({ EmptyState: ({ children }: { children: React.ReactNode }) => <div data-testid="welcome">{children}</div> }));
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
import { useChatStore } from "@/stores/chatStore";

const th = dictionaries.th;
const initial = useChatStore.getState();
const settle = async () => {
  for (let i = 0; i < 4; i++) await act(() => new Promise<void>((r) => setTimeout(r, 0)));
};

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
  mockApi.listMessages.mockResolvedValue({ messages: [], has_more: false });
});
afterEach(() => {
  cleanup();
  useI18nStore.setState({ locale: "en" });
});

describe("FIX132 F17 — a link that is not a conversation", () => {
  for (const bad of ["undefined", "null", "abc", "1234"]) {
    it(`/chat/${bad}: Thai "no such chat" with a new-chat button, no composer, no request`, async () => {
      params.conversationId = bad;
      render(<ConversationPage />);
      await settle();
      const box = screen.getByTestId("conversation-not-found");
      expect(box).toHaveTextContent(th["chat.notFound.title"]);
      expect(box).toHaveTextContent(th["chat.notFound.body"]);
      expect(th["chat.notFound.title"]).toMatch(/ไม่พบแชท/);
      expect(screen.queryByTestId("composer")).toBeNull();
      expect(screen.queryByTestId("welcome")).toBeNull();
      expect(mockApi.listMessages).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: th["chat.notFound.newChat"] }));
      expect(push).toHaveBeenCalledWith("/chat");
    });
  }

  it("a real conversation id still loads as before", async () => {
    params.conversationId = "0b7f7d0e-6a0e-4b8a-9d61-0c7a1f2b3c4d";
    render(<ConversationPage />);
    await settle();
    expect(mockApi.listMessages).toHaveBeenCalled();
    expect(screen.queryByTestId("conversation-not-found")).toBeNull();
  });
});
