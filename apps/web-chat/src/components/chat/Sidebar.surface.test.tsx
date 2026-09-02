// FIX43 Part B3 — the account menu is gated per SECTION, not per page.
//
// Before: four entries (Scheduled tasks, Connectors, Writing style,
// Settings) were hidden from the browser as one block, although two of them
// have a web half. After: the web menu shows Scheduled tasks and Settings;
// Connectors and Writing style (all host-local state, FIX43_REPORT §B2) stay
// desktop-only. Also pins that these labels go through the dictionary.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: false }));
const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listConversations: vi.fn(),
    listProjects: vi.fn(),
    searchConversations: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat",
}));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "somchai@acme.ai.gateway" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";

import { Sidebar } from "./Sidebar";

const initialState = useChatStore.getState();

function openMenu() {
  render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);
  fireEvent.click(screen.getByRole("button", { expanded: false }));
  return screen.getByRole("menu");
}

const labels = (menu: HTMLElement) => [...menu.querySelectorAll('[role="menuitem"]')].map((el) => el.textContent?.trim() ?? "");

afterEach(cleanup);
beforeEach(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  state.hosted = false;
  useChatStore.setState({ ...initialState, conversationsLoaded: true }, true);
  useI18nStore.setState({ locale: "en" });
  localStorage.clear();
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
});

describe("FIX43 — account menu per surface", () => {
  it("web: Scheduled tasks and Settings are there; Connectors and Writing style are not", () => {
    const items = labels(openMenu());
    expect(items).toContain("Scheduled tasks");
    expect(items).toContain("Settings");
    expect(items).toContain("Memory");
    expect(items).not.toContain("Connectors");
    expect(items).not.toContain("Writing style");
    // Not shown as disabled either — absent means absent.
    expect(screen.queryByText("Connectors")).toBeNull();
  });

  it("desktop: all four, in the same order as before", () => {
    state.hosted = true;
    const items = labels(openMenu());
    const i = (s: string) => items.indexOf(s);
    expect(i("Scheduled tasks")).toBeGreaterThan(-1);
    expect(i("Connectors")).toBeGreaterThan(i("Scheduled tasks"));
    expect(i("Writing style")).toBeGreaterThan(i("Connectors"));
    expect(i("Settings")).toBeGreaterThan(i("Writing style"));
  });

  it("the labels come from the dictionary (Thai renders Thai)", () => {
    state.hosted = true;
    useI18nStore.setState({ locale: "th" });
    const items = labels(openMenu());
    for (const key of ["menu.scheduledTasks", "menu.connectors", "menu.writingStyle", "menu.settings", "menu.memory"] as const) {
      expect(items).toContain(dictionaries.th[key]);
      expect(dictionaries.th[key]).not.toBe(dictionaries.en[key]);
    }
  });
});
