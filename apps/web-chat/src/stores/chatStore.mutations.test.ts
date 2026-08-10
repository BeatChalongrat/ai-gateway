// chatStore mutation tests — optimistic updates + server-error
// rollback for the sidebar operations (create / rename / delete /
// pin / model switch / project move). The api module is mocked;
// these tests assert store-state transitions only.

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Conversation, Message, Project } from "@/lib/types";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    createConversation: vi.fn(),
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setConversationPinned: vi.fn(),
    setConversationModel: vi.fn(),
    setConversationMemory: vi.fn(),
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

const msg = (id: string): Message => ({
  id,
  role: "user",
  content: "hello",
  status: "completed",
  created_at: "2026-01-01T00:00:00Z",
});

const state = () => useChatStore.getState();
const findConv = (id: string) => state().conversations.find((c) => c.id === id);

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
});

describe("createConversation", () => {
  it("prepends the new conversation, seeds an empty loaded message slot, and activates it", async () => {
    useChatStore.setState({ conversations: [conv("old")] });
    mockApi.createConversation.mockResolvedValue(conv("new"));

    const created = await state().createConversation("claude-sonnet-4");

    expect(created.id).toBe("new");
    expect(state().conversations.map((c) => c.id)).toEqual(["new", "old"]);
    expect(state().messages["new"]).toEqual([]);
    expect(state().messagesLoaded["new"]).toBe(true);
    expect(state().activeConversationId).toBe("new");
  });

  it("temporary chat consumes the nextConversationTemporary flag and disables memory", async () => {
    mockApi.createConversation.mockResolvedValue(conv("t1"));
    mockApi.setConversationMemory.mockResolvedValue(
      conv("t1", { memory_enabled: false }),
    );
    state().setNextConversationTemporary(true);

    await state().createConversation("claude-sonnet-4");

    expect(mockApi.setConversationMemory).toHaveBeenCalledWith("t1", false);
    expect(state().nextConversationTemporary).toBe(false);
  });
});

describe("renameConversation", () => {
  it("merges the server response into the row", async () => {
    useChatStore.setState({ conversations: [conv("c1")] });
    mockApi.renameConversation.mockResolvedValue(
      conv("c1", { title: "Renamed" }),
    );

    await state().renameConversation("c1", "Renamed");

    expect(findConv("c1")?.title).toBe("Renamed");
  });

  it("propagates a server failure without touching the row (no optimistic write)", async () => {
    useChatStore.setState({ conversations: [conv("c1")] });
    mockApi.renameConversation.mockRejectedValue(new Error("500"));

    await expect(state().renameConversation("c1", "X")).rejects.toThrow();
    expect(findConv("c1")?.title).toBe("Conv c1");
  });
});

describe("deleteConversation", () => {
  it("removes the row, its message slots, deactivates it, and decrements the project count", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" }), conv("c2")],
      messages: { c1: [msg("m1")], c2: [msg("m2")] },
      messagesLoaded: { c1: true, c2: true },
      projects: [project("p1", 3)],
      activeConversationId: "c1",
    });
    mockApi.deleteConversation.mockResolvedValue(new Response(null, { status: 204 }));

    await state().deleteConversation("c1");

    expect(state().conversations.map((c) => c.id)).toEqual(["c2"]);
    expect(state().messages["c1"]).toBeUndefined();
    expect(state().messagesLoaded["c1"]).toBeUndefined();
    expect(state().activeConversationId).toBeNull();
    expect(state().projects[0].conversation_count).toBe(2);
    // Untouched sibling survives.
    expect(state().messages["c2"]).toHaveLength(1);
  });

  it("leaves state intact when the server delete fails", async () => {
    useChatStore.setState({
      conversations: [conv("c1")],
      messages: { c1: [msg("m1")] },
      messagesLoaded: { c1: true },
    });
    mockApi.deleteConversation.mockRejectedValue(new Error("500"));

    await expect(state().deleteConversation("c1")).rejects.toThrow();
    expect(state().conversations).toHaveLength(1);
    expect(state().messages["c1"]).toHaveLength(1);
  });
});

describe("togglePinConversation", () => {
  it("flips optimistically and reconciles with the server payload", async () => {
    useChatStore.setState({ conversations: [conv("c1", { is_pinned: false })] });
    let resolveServer!: (v: Conversation) => void;
    mockApi.setConversationPinned.mockReturnValue(
      new Promise<Conversation>((r) => {
        resolveServer = r;
      }),
    );

    const p = state().togglePinConversation("c1");
    // Optimistic flip is visible before the server answers.
    expect(findConv("c1")?.is_pinned).toBe(true);
    resolveServer(conv("c1", { is_pinned: true }));
    await p;
    expect(findConv("c1")?.is_pinned).toBe(true);
    expect(mockApi.setConversationPinned).toHaveBeenCalledWith("c1", true);
  });

  it("rolls back the flip and raises the banner on failure", async () => {
    useChatStore.setState({ conversations: [conv("c1", { is_pinned: false })] });
    mockApi.setConversationPinned.mockRejectedValue(new Error("500"));

    await state().togglePinConversation("c1");

    expect(findConv("c1")?.is_pinned).toBe(false);
    expect(state().errorBanner).toBe("Couldn't update pin. Please try again.");
  });
});

describe("setConversationModel", () => {
  it("applies optimistically and keeps the server merge on success", async () => {
    useChatStore.setState({ conversations: [conv("c1")] });
    mockApi.setConversationModel.mockResolvedValue(
      conv("c1", { model_id: "gpt-4o" }),
    );

    await state().setConversationModel("c1", "gpt-4o");
    expect(findConv("c1")?.model_id).toBe("gpt-4o");
  });

  it("rolls back to the previous model and rethrows on failure", async () => {
    useChatStore.setState({ conversations: [conv("c1")] });
    mockApi.setConversationModel.mockRejectedValue(new Error("500"));

    await expect(state().setConversationModel("c1", "gpt-4o")).rejects.toThrow();
    expect(findConv("c1")?.model_id).toBe("claude-sonnet-4");
  });

  it("upserts the row from the server response when the conversation wasn't in the sidebar yet", async () => {
    useChatStore.setState({ conversations: [] });
    mockApi.setConversationModel.mockResolvedValue(
      conv("fresh", { model_id: "gpt-4o" }),
    );

    await state().setConversationModel("fresh", "gpt-4o");
    expect(findConv("fresh")?.model_id).toBe("gpt-4o");
  });
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

    expect(findConv("c1")?.project_id).toBe("p2");
    expect(state().projects.find((p) => p.id === "p1")?.conversation_count).toBe(1);
    expect(state().projects.find((p) => p.id === "p2")?.conversation_count).toBe(1);
  });

  it("rolls back membership AND counts on failure, raising the banner", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 2), project("p2", 0)],
    });
    mockApi.moveConversationToProject.mockRejectedValue(new Error("500"));

    await state().moveConversationToProject("c1", "p2");

    expect(findConv("c1")?.project_id).toBe("p1");
    expect(state().projects.find((p) => p.id === "p1")?.conversation_count).toBe(2);
    expect(state().projects.find((p) => p.id === "p2")?.conversation_count).toBe(0);
    expect(state().errorBanner).toBeTruthy();
  });

  it("no-ops when the target project equals the current one", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 2)],
    });

    await state().moveConversationToProject("c1", "p1");
    expect(mockApi.moveConversationToProject).not.toHaveBeenCalled();
  });

  it("un-assigns with null and only decrements the source project", async () => {
    useChatStore.setState({
      conversations: [conv("c1", { project_id: "p1" })],
      projects: [project("p1", 2)],
    });
    mockApi.moveConversationToProject.mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await state().moveConversationToProject("c1", null);

    expect(findConv("c1")?.project_id).toBeNull();
    expect(state().projects[0].conversation_count).toBe(1);
  });
});
