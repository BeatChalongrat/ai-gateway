// Phase-4 projects wire-shape tests — pin the URL + body contract
// for createProject and moveConversationToProject against the
// chat-edge Projects API.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRefreshSession, mockRedirect } = vi.hoisted(() => ({
  mockRefreshSession: vi.fn(),
  mockRedirect: vi.fn(),
}));

vi.mock("./auth", () => ({
  refreshSession: mockRefreshSession,
  redirectToPortalLogin: mockRedirect,
}));

import { api } from "./api";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.createProject", () => {
  it("POSTs /projects with name + instructions and unwraps the envelope", async () => {
    const projectRow = {
      id: "p1",
      name: "Website redesign",
      instructions: "Be concise.",
      knowledge_base_id: null,
      conversation_count: 0,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ project: projectRow }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const res = await api.createProject("Website redesign", "Be concise.");

    expect(res.project).toEqual(projectRow);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toMatch(/\/projects$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      name: "Website redesign",
      instructions: "Be concise.",
    });
  });
});

describe("api.moveConversationToProject", () => {
  it("PUTs /conversations/{id}/project with the project id", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const res = await api.moveConversationToProject("c1", "p1");

    expect(res.status).toBe(204);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toMatch(/\/conversations\/c1\/project$/);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ project_id: "p1" });
  });

  it("sends project_id null on un-assign", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await api.moveConversationToProject("c1", null);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ project_id: null });
  });
});
