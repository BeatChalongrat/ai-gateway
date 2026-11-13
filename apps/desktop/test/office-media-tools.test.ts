import { describe, expect, it, vi, afterEach } from "vitest";
import { officeMediaTool } from "../src/main/office-media-tools";
import { ChatEdgeClient } from "../src/main/chat-edge-client";
import type { Logger } from "../src/main/logger";

const id = "00000000-0000-4000-8000-000000000001";
const other = "00000000-0000-4000-8000-000000000002";
const ctx = { conversationId: id, toolCallId: "test", signal: new AbortController().signal };
const client = (request: ReturnType<typeof vi.fn>) => ({ officeMediaRequest: request }) as unknown as ChatEdgeClient;
afterEach(() => vi.unstubAllGlobals());

describe("Office/media agent tools", () => {
  it("supports visual review with a bounded long timeout and strips binary tool text", async () => {
    const request = vi.fn().mockResolvedValue({ visual: { fields: [{ id: "p", value: "safe text" }], background: "base64-background", pages: [{ image: "base64-page" }], blocks: [{ image: "base64-inline" }] }, review: { status: "unchanged" } });
    const result = await officeMediaTool("edit_office_file", { file_id: id, mode: "ai_repair", sha256: "hash" }, ctx, client(request));
    expect(request.mock.calls[0][3]).toBe(660_000);
    expect(request.mock.calls[0][1].mode).toBe("ai_propose");
    expect(result.text).toContain("safe text");
    expect(result.text).not.toContain("base64");
    expect(result.attachedFiles).toBeUndefined();
  });
  it("pins editor operations to the host conversation and attaches the saved revision", async () => {
    const file = { id: other, filename: "test.docx", content_type: "x", file_size_bytes: 3 };
    const request = vi.fn().mockResolvedValue({ file });
    const result = await officeMediaTool("edit_office_file", { file_id: id, mode: "edit", conversation_id: other }, ctx, client(request));
    expect(request.mock.calls[0][1].conversation_id).toBe(id);
    expect(result.attachedFiles).toEqual([file]);
  });
  it("stops before submit when capability is unavailable", async () => {
    const request = vi.fn().mockResolvedValue({ enabled: false, reason: "Not configured" });
    expect((await officeMediaTool("generate_video", { prompt: "A tree" }, ctx, client(request))).ok).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
  });
  it("returns pending confirmation without approving it", async () => {
    const request = vi.fn().mockResolvedValueOnce({ enabled: true, agent_model: "configured-model", supported_controls: { duration_sec: [1, 10] } }).mockResolvedValueOnce({ job_id: id, status: "pending_confirm" });
    const result = await officeMediaTool("generate_video", { prompt: "A tree", duration_sec: 5 }, ctx, client(request));
    expect(result.ok).toBe(true); expect(result.text).toContain("pending_confirm");
    expect(request.mock.calls.map(call => call[0])).toEqual(["/video/config", "/videos/generations"]);
    expect(request.mock.calls[1][1].conversation_id).toBe(id);
  });
  it("rejects confirmation actions and jobs from other conversations", async () => {
    const request = vi.fn().mockResolvedValue({ jobs: [] });
    expect((await officeMediaTool("video_job", { job_id: id, action: "confirm" }, ctx, client(request))).ok).toBe(false);
    expect(request).not.toHaveBeenCalled();
    expect((await officeMediaTool("video_job", { job_id: id, action: "cancel" }, ctx, client(request))).ok).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
  });
  it("rejects out-of-range durations without clamping or submitting", async () => {
    const request = vi.fn().mockResolvedValue({ enabled: true, agent_model: "model", supported_controls: { duration_sec: [1, 5] } });
    expect((await officeMediaTool("generate_video", { prompt: "A tree", duration_sec: 6 }, ctx, client(request))).ok).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
  });
  it("uses the API prefix, host token and cancellation signal", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"enabled":false}', { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const c = new ChatEdgeClient("https://chat.example", () => "test-token", { warn: vi.fn() } as unknown as Logger);
    await c.officeMediaRequest("/video/config", undefined, ctx.signal);
    expect(fetcher.mock.calls[0][0]).toBe("https://chat.example/api/v1/video/config");
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer test-token");
    expect(fetcher.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  });
});
