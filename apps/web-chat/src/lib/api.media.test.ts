// Phase 11 A3 — media-gen playback must go through the AUTHED client.
//
// VideoGenForm/AudioGenForm previously set <video|audio src={job.output_url}>
// directly — a raw gateway-relative URL that can't authenticate and doesn't
// even resolve from the web-chat origin. The fix routes playback through
// api.mediaJobOutputObjectUrl: cookie-authed raw() (credentials:"include",
// 401 → refresh → retry) → blob → object URL. These tests pin that contract
// at the lib level (the current vitest harness cannot compile .tsx imports,
// so the component wiring is covered by the forms calling this method).

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

import { api, ApiError } from "./api";

let fetchMock: ReturnType<typeof vi.fn>;
let createSpy: ReturnType<typeof vi.fn>;
const originalCreate = URL.createObjectURL;

beforeEach(() => {
  vi.clearAllMocks();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  // jsdom doesn't implement URL.createObjectURL — observable stub.
  createSpy = vi.fn(() => "blob:mock-artifact-1");
  (URL as unknown as Record<string, unknown>).createObjectURL = createSpy;
});

afterEach(() => {
  vi.unstubAllGlobals();
  (URL as unknown as Record<string, unknown>).createObjectURL = originalCreate;
});

describe("api.mediaJobOutputObjectUrl (authed media playback)", () => {
  it("fetches the artifact from the chat-edge proxy with cookie credentials and returns an object URL", async () => {
    fetchMock.mockResolvedValue(
      new Response(new Blob([new Uint8Array([1, 2, 3])], { type: "video/mp4" }), {
        status: 200,
      }),
    );

    const url = await api.mediaJobOutputObjectUrl("j1");

    // Bytes requested via the authed client against the chat-edge
    // origin's /jobs/{id}/output proxy — NOT the raw gateway path
    // ("/v1/jobs/j1/output") that job.output_url carries.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl, init] = fetchMock.mock.calls[0];
    expect(calledUrl).toBe("http://localhost:4000/api/v1/jobs/j1/output");
    expect(init).toMatchObject({ credentials: "include" });

    // The playback src the forms use is the object URL of the blob.
    expect(url).toBe("blob:mock-artifact-1");
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  it("rides the 401 → silent-refresh → retry contract like every authed call", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(new Blob([new Uint8Array([9])], { type: "audio/mpeg" }), {
          status: 200,
        }),
      );
    mockRefreshSession.mockResolvedValue("ok");

    const url = await api.mediaJobOutputObjectUrl("j2");

    expect(url).toBe("blob:mock-artifact-1");
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://localhost:4000/api/v1/jobs/j2/output",
    );
  });

  it("throws a typed ApiError (and creates no object URL) when the fetch fails", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 404, statusText: "Not Found" }));

    const err = await api.mediaJobOutputObjectUrl("j3").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "HTTP_404" });
    expect(createSpy).not.toHaveBeenCalled();
  });
});
