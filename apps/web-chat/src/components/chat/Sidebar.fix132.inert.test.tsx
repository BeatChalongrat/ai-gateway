// FIX132 F16 — hidden panels left the Tab order. NIGHT3 j6 at 360 px: Tab
// stops 12–23 of the first 23 landed on the CLOSED sidebar (x = -298) and
// its row menus (x = -43) — a keyboard user walked through a panel they
// could not see. The closed drawer (phone) and the collapsed sidebar
// (desktop) are now `inert`: out of the Tab order and the accessibility
// tree. The open drawer and the desktop sidebar are untouched.
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({ mockApi: { listConversations: vi.fn(), listProjects: vi.fn() } }));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/chat" }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "a@b.c" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));

import { useChatStore } from "@/stores/chatStore";

import { Sidebar } from "./Sidebar";

const initialState = useChatStore.getState();
function viewport(desktop: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: /min-width:\s*768px/.test(q) ? desktop : false,
    media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
const aside = (c: HTMLElement) => c.querySelector("aside") as HTMLElement;
const mount = (open: boolean, collapsed: boolean) =>
  render(<Sidebar activeId={null} open={open} collapsed={collapsed} onClose={() => {}} onToggleCollapsed={() => {}} />).container;

beforeEach(() => {
  useChatStore.setState({ ...initialState, conversationsLoaded: true }, true);
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
});
afterEach(cleanup);

describe("FIX132 F16 — a hidden sidebar is inert", () => {
  it("phone, drawer closed: inert", () => {
    viewport(false);
    expect(aside(mount(false, false)).hasAttribute("inert")).toBe(true);
  });
  it("phone, drawer open: not inert", () => {
    viewport(false);
    expect(aside(mount(true, false)).hasAttribute("inert")).toBe(false);
  });
  it("desktop, collapsed: inert", () => {
    viewport(true);
    expect(aside(mount(false, true)).hasAttribute("inert")).toBe(true);
  });
  it("desktop, expanded: not inert, whatever the drawer flag says", () => {
    viewport(true);
    expect(aside(mount(false, false)).hasAttribute("inert")).toBe(false);
  });
});
