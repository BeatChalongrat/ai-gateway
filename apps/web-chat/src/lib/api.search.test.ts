// api.searchMessages wire-shape test — pins the /search query contract
// (q + scope=messages + limit) the sidebar's content search depends on.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockRefreshSession, mockRedirect } = vi.hoisted(() => ({
  mockRefreshSession: vi.fn(),
  mockRedirect: vi.fn(),
}));

vi.mock("./auth", () => ({
  refreshSession: mockRefreshSession,
  redirectToPortalLogin: mockRedirect,
  HOSTED_MODE: false,
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

describe("api.searchMessages", () => {
  it("hits /search with q, scope=messages and the given limit", async () => {
    const body = { query: "รายงาน", scope: "messages", results: [], total: 0 };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const res = await api.searchMessages("รายงาน", 20);

    expect(res).toEqual(body);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/search?");
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("q")).toBe("รายงาน");
    expect(params.get("scope")).toBe("messages");
    expect(params.get("limit")).toBe("20");
  });

  it("defaults limit to 20", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ query: "", scope: "messages", results: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await api.searchMessages("x");
    const params = new URL(
      String(fetchMock.mock.calls[0][0]),
      "http://localhost",
    ).searchParams;
    expect(params.get("limit")).toBe("20");
  });
});
