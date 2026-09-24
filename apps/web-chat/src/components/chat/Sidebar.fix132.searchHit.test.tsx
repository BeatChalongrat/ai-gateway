// FIX132 F12 — a content hit in the sidebar search opens the chat AT the
// matching message: the URL carries #msg-<id> (reload / back keep it) and a
// jump is requested, which the conversation page acts on (a hit inside the
// chat that is already open included — a same-path push fires no event).
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, push } = vi.hoisted(() => ({
  mockApi: { listConversations: vi.fn(), listProjects: vi.fn() },
  push: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), usePathname: () => "/chat" }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "a@b.c" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));

import { useI18nStore } from "@/lib/i18n";
import { useMessageJumpStore } from "@/lib/messageJump";
import { useChatStore } from "@/stores/chatStore";

import { Sidebar } from "./Sidebar";

const initialState = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  useI18nStore.setState({ locale: "th" });
  useMessageJumpStore.setState({ request: null });
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
  useChatStore.setState({ ...initialState, conversationsLoaded: true }, true);
});
// The sidebar clears the search on mount (its box is empty), so the hits
// are put in after it has mounted — as a finished search would.
function showHits(hits: ReturnType<typeof useChatStore.getState>["conversationsSearchHits"]) {
  render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);
  act(() => useChatStore.setState({ conversationsSearchActive: true, conversationsSearchResults: [], conversationsSearchHits: hits }));
}
afterEach(cleanup);

describe("FIX132 F12 — a search hit opens the chat at the message", () => {
  it("navigates to /chat/<conv>#msg-<message> and requests the jump", () => {
    showHits([{ conversation_id: "c9", message_id: "m16", conversation_title: "ทริปเกียวโต", snippet: "ศาลเจ้าฟูชิมิ", date: "2026-09-01" }]);
    fireEvent.click(screen.getByText("ทริปเกียวโต"));
    expect(push).toHaveBeenCalledWith("/chat/c9#msg-m16");
    expect(useMessageJumpStore.getState().request).toMatchObject({ convId: "c9", target: "m16" });
  });

  it("a hit without a message id (an older server) still opens the chat", () => {
    showHits([{ conversation_id: "c9", conversation_title: "ทริปเกียวโต", snippet: "x", date: "2026-09-01" }]);
    fireEvent.click(screen.getByText("ทริปเกียวโต"));
    expect(push).toHaveBeenCalledWith("/chat/c9");
    expect(useMessageJumpStore.getState().request).toBeNull();
  });
});
