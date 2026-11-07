// FIX132 F29 — "สร้างโปรเจกต์ใหม่" created a project called "โปรเจกต์ใหม่" at
// once, silently: no dialog, the URL stayed /chat, focus stayed on the
// button (NIGHT3 p-01). Now it asks for the name first (like ChatGPT's
// "New project"), creates nothing until a name is given, and opens the new
// project. The row menu's "move to a new project" asks the same way.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, push } = vi.hoisted(() => ({
  mockApi: { listConversations: vi.fn(), listProjects: vi.fn(), createProject: vi.fn() },
  push: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), usePathname: () => "/chat" }));
vi.mock("@/hooks/useAuth", () => ({ useAuth: () => ({ user: { email: "a@b.c" } }) }));
vi.mock("@/hooks/useKeyboardShortcuts", () => ({ useNewChatHandler: () => vi.fn() }));
vi.mock("@/hooks/useMemoryPendingCount", () => ({ useMemoryPendingStore: () => 0 }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";

import { Sidebar } from "./Sidebar";

const th = dictionaries.th;
const initialState = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initialState, conversationsLoaded: true }, true);
  mockApi.listConversations.mockResolvedValue({ conversations: [], has_more: false, next_cursor: null });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
  mockApi.createProject.mockImplementation(async (name: string) => ({
    project: { id: "p-new", name, instructions: "", conversation_count: 0, created_at: "", updated_at: "" },
  }));
});
afterEach(cleanup);

const mount = () => render(<Sidebar activeId={null} open collapsed={false} onClose={() => {}} onToggleCollapsed={() => {}} />);

describe("FIX132 F29 — a new project asks for its name, then opens", () => {
  it("the button opens a name dialog and creates nothing yet", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: th["project.new"] }));
    const dialog = screen.getByRole("dialog");
    const input = screen.getByLabelText(th["project.nameLabel"]);
    expect(dialog).toContainElement(input);
    expect(document.activeElement).toBe(input);
    expect(mockApi.createProject).not.toHaveBeenCalled();
    // An empty name cannot be created.
    expect(screen.getByRole("button", { name: th["project.createAction"] })).toBeDisabled();
  });

  it("a name + Enter creates the project with that name and opens it", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: th["project.new"] }));
    const input = screen.getByLabelText(th["project.nameLabel"]);
    fireEvent.change(input, { target: { value: "  ทริปญี่ปุ่น  " } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mockApi.createProject).toHaveBeenCalledWith("ทริปญี่ปุ่น", "");
    expect(push).toHaveBeenCalledWith("/chat/projects?project=p-new");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancel creates nothing", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: th["project.new"] }));
    fireEvent.click(screen.getByRole("button", { name: th["common.cancel"] }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockApi.createProject).not.toHaveBeenCalled();
  });
});
