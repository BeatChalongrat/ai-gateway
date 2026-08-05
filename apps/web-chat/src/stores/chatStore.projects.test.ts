// chatStore Phase-4 project tests — optimistic move + rollback for
// moveConversationToProject, and the createConversation →
// nextConversationProjectId assignment contract. The api module is
// mocked; these tests assert store-state transitions only.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { dictionaries } from "@/lib/i18n/dict";
import type { Conversation, Project } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    createConversation: vi.fn(),
    setConversationMemory: vi.fn(),
    listProjects: vi.fn(),
    createProject: vi.fn(),
    updateProject: vi.fn(),
    setProjectKnowledgeBase: vi.fn(),
    deleteProject: vi.fn(),
    moveConversationToProject: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});

vi.mock("@/hooks/useUsage", () => ({
  bumpUsageVersion: vi.fn(),
}));

import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

function conv(id: string, extra?: Partial<Conversation>): Conversation {
  return {
    id,
    title: `Conv ${id}`,
    model_id: "claude-sonnet-4",
    message_count: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...extra,
  };
}

function project(id: string, count: number): Project {
  return {
    id,
    name: `Project ${id}`,
    instructions: "",
    knowledge_base_id: null,
    conversation_count: count,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const state = () => useChatStore.getState();
const findConv = (id: string) => state().conversations.find((c) => c.id === id);
const findProject = (id: string) => state().projects.find((p) => p.id === id);

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
});

describe("moveConversationToProject", () => {
  it("re-assigns optimistically, adjusting both project counts", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 2), project("p2", 0)],
    });
    mockApi.moveConversationToProject.mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await state().moveConversationToProject("c1", "p2");

    expect(mockApi.moveConversationToProject).toHaveBeenCalledWith("c1", "p2");
    expect(findConv("c1")?.project_id).toBe("p2");
    expect(findProject("p1")?.conversation_count).toBe(1);
    expect(findProject("p2")?.conversation_count).toBe(1);
  });

  it("rolls back membership AND counts on failure, raising the banner", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 2), project("p2", 0)],
    });
    mockApi.moveConversationToProject.mockRejectedValue(new Error("500"));

    await state().moveConversationToProject("c1", "p2");

    expect(findConv("c1")?.project_id).toBe("p1");
    expect(findProject("p1")?.conversation_count).toBe(2);
    expect(findProject("p2")?.conversation_count).toBe(0);
    expect(state().errorBanner).toBe(
      dictionaries.en["projects.error.moveFailed"],
    );
  });

  it("un-assigns with null and decrements the previous project's count", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 1)],
    });
    mockApi.moveConversationToProject.mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await state().moveConversationToProject("c1", null);

    expect(mockApi.moveConversationToProject).toHaveBeenCalledWith("c1", null);
    expect(findConv("c1")?.project_id).toBeNull();
    expect(findProject("p1")?.conversation_count).toBe(0);
  });

  it("no-ops when the target project equals the current one", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 1)],
    });

    await state().moveConversationToProject("c1", "p1");
    expect(mockApi.moveConversationToProject).not.toHaveBeenCalled();
  });
});

describe("createConversation with nextConversationProjectId", () => {
  it("assigns the fresh conversation to the flagged project and clears the flag", async () => {
    useChatStore.setState({ projects: [project("p1", 0)] });
    mockApi.createConversation.mockResolvedValue(conv("new"));
    mockApi.moveConversationToProject.mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    state().setNextConversationProjectId("p1");

    const created = await state().createConversation("claude-sonnet-4");

    expect(mockApi.moveConversationToProject).toHaveBeenCalledWith("new", "p1");
    expect(created.project_id).toBe("p1");
    expect(findConv("new")?.project_id).toBe("p1");
    expect(findProject("p1")?.conversation_count).toBe(1);
    expect(state().nextConversationProjectId).toBeNull();
  });

  it("survives a failing move (non-fatal) and still clears the flag", async () => {
    useChatStore.setState({ projects: [project("p1", 0)] });
    mockApi.createConversation.mockResolvedValue(conv("new"));
    mockApi.moveConversationToProject.mockRejectedValue(new Error("500"));
    state().setNextConversationProjectId("p1");

    const created = await state().createConversation("claude-sonnet-4");

    expect(created.id).toBe("new");
    expect(findConv("new")?.project_id).toBeUndefined();
    expect(findProject("p1")?.conversation_count).toBe(0);
    expect(state().nextConversationProjectId).toBeNull();
  });

  it("does not touch the project API when no flag is set", async () => {
    mockApi.createConversation.mockResolvedValue(conv("new"));

    await state().createConversation("claude-sonnet-4");

    expect(mockApi.moveConversationToProject).not.toHaveBeenCalled();
  });
});
