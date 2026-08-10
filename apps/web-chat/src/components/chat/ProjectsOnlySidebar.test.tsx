// Phase 9 — Folders were merged into Projects.
//
// What must hold in the UI after the merge:
//   * the sidebar has ONE organiser — no folder section, no "New folder";
//   * a project with NO instructions and NO knowledge base renders as a
//     plain grouping section (that is what a folder became);
//   * conversations group under their project_id and do NOT also appear
//     in the time buckets;
//   * "Move to project" is the only move affordance, and it can create a
//     new project on the spot (the shortcut the folder submenu owned).

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listConversations: vi.fn(),
    listProjects: vi.fn(),
    createProject: vi.fn(),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
    moveConversationToProject: vi.fn(),
    searchConversations: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat",
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ user: { email: "somchai@acme.ai.gateway" } }),
}));

vi.mock("@/hooks/useKeyboardShortcuts", () => ({
  useNewChatHandler: () => vi.fn(),
}));

vi.mock("@/hooks/useMemoryPendingCount", () => ({
  useMemoryPendingStore: () => 0,
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation, Project } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { Sidebar } from "./Sidebar";

const initialState = useChatStore.getState();

function conv(id: string, extra?: Partial<Conversation>): Conversation {
  return {
    id,
    title: `Conv ${id}`,
    model_id: "claude-sonnet-4",
    message_count: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

/** A project with no instructions and no KB — the migrated folder. */
function bareProject(id: string, name: string, count = 1): Project {
  return {
    id,
    name,
    instructions: "",
    knowledge_base_id: null,
    conversation_count: count,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function renderSidebar() {
  return render(
    <Sidebar
      activeId={null}
      open
      collapsed={false}
      onClose={() => {}}
      onToggleCollapsed={() => {}}
    />,
  );
}

afterEach(cleanup);
beforeEach(() => {
  // jsdom has no matchMedia; the sidebar mounts a Modal that probes it.
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false,
      media: q,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  localStorage.clear();
  mockApi.listConversations.mockResolvedValue({
    conversations: [],
    has_more: false,
    next_cursor: null,
  });
  mockApi.listProjects.mockResolvedValue({ projects: [] });
});

describe("sidebar has one organiser", () => {
  it("shows no folder section and no New-folder button", () => {
    useChatStore.setState({
      conversationsLoaded: true,
      projectsLoaded: true,
      conversations: [conv("c1")],
      projects: [],
    });
    renderSidebar();

    expect(screen.queryByText(/new folder/i)).toBeNull();
    expect(screen.queryByLabelText("Folder actions")).toBeNull();
    expect(screen.getByText(dictionaries.en["project.new"])).toBeInTheDocument();
  });

  it("renders an instruction-less, KB-less project as a plain grouping section", () => {
    useChatStore.setState({
      conversationsLoaded: true,
      projectsLoaded: true,
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [bareProject("p1", "Migrated folder")],
    });
    renderSidebar();

    expect(screen.getByText("Migrated folder")).toBeInTheDocument();
    // The live count renders next to the name — no instructions needed.
    expect(screen.getByText("(1)")).toBeInTheDocument();
    expect(screen.getByLabelText("Project actions")).toBeInTheDocument();
    // Its chat is listed under it.
    expect(screen.getByText("Conv c1")).toBeInTheDocument();
  });

  it("groups by project_id, and a chat in a project does not also appear in a time bucket", () => {
    useChatStore.setState({
      conversationsLoaded: true,
      projectsLoaded: true,
      conversations: [conv("c1", { project_id: "p1" }), conv("c2")],
      projects: [bareProject("p1", "Work")],
    });
    renderSidebar();

    // Exactly one row for the project's chat.
    expect(screen.getAllByText("Conv c1")).toHaveLength(1);
    // The unassigned chat still flows into the time buckets.
    expect(screen.getByText("Conv c2")).toBeInTheDocument();
  });

  it("ignores a stale folder_id — grouping is project_id only", () => {
    useChatStore.setState({
      conversationsLoaded: true,
      projectsLoaded: true,
      // Pre-migration row shape: folder_id still set, project_id null.
      conversations: [conv("c1", { folder_id: "f-old" })],
      projects: [bareProject("p1", "Work", 0)],
    });
    renderSidebar();

    // The chat is NOT swallowed by the merge — it shows in the buckets.
    expect(screen.getByText("Conv c1")).toBeInTheDocument();
    expect(screen.getByText("Work")).toBeInTheDocument();
  });
});

describe("creating a project from the sidebar", () => {
  it("creates an EMPTY project (grouping only, no instructions)", async () => {
    useChatStore.setState({ conversationsLoaded: true, projectsLoaded: true });
    mockApi.createProject.mockResolvedValue({
      project: bareProject("p-new", "New project", 0),
    });
    renderSidebar();

    await act(async () => {
      fireEvent.click(screen.getByText(dictionaries.en["project.new"]));
    });

    expect(mockApi.createProject).toHaveBeenCalledWith(
      dictionaries.en["project.defaultName"],
      "",
    );
  });

  it("surfaces a failure instead of silently doing nothing", async () => {
    useChatStore.setState({ conversationsLoaded: true, projectsLoaded: true });
    mockApi.createProject.mockRejectedValue(new Error("500"));
    renderSidebar();

    await act(async () => {
      fireEvent.click(screen.getByText(dictionaries.en["project.new"]));
    });

    expect(useChatStore.getState().errorBanner).toBe(
      dictionaries.en["projects.toast.createFailed"],
    );
  });
});

describe("Thai locale", () => {
  it("labels the organiser in Thai", () => {
    act(() => {
      useI18nStore.setState({ locale: "th" });
    });
    useChatStore.setState({ conversationsLoaded: true, projectsLoaded: true });
    renderSidebar();

    expect(screen.getByText(dictionaries.th["project.new"])).toBeInTheDocument();
    expect(dictionaries.th["project.new"]).not.toBe(dictionaries.en["project.new"]);
  });
});
