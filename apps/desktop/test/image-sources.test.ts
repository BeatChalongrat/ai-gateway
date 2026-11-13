import { describe, expect, it, vi } from "vitest";
import { imageSources } from "../src/main/image-sources";
import type { ChatEdgeClient } from "../src/main/chat-edge-client";

const id = "00000000-0000-4000-8000-000000000001";
function client(bytes = Buffer.from("image"), mime = "image/png") {
  return { listFiles: vi.fn(async () => [{ id }]), downloadFileBytes: vi.fn(async () => ({ bytes, mime })) };
}
const signal = new AbortController().signal;

describe("conversation image edit inputs", () => {
  it("preserves bytes and deduplicates references", async () => {
    const c = client();
    expect(await imageSources(c as unknown as ChatEdgeClient, "conversation", [id, id], signal)).toEqual(["data:image/png;base64,aW1hZ2U="]);
    expect(c.listFiles).toHaveBeenCalledWith("conversation");
    expect(c.downloadFileBytes).toHaveBeenCalledTimes(1);
  });
  it("rejects another conversation's file before download", async () => {
    const c = client(); c.listFiles.mockResolvedValue([]);
    await expect(imageSources(c as unknown as ChatEdgeClient, "conversation", [id], signal)).rejects.toThrow("belong");
    expect(c.downloadFileBytes).not.toHaveBeenCalled();
  });
  it.each([[Buffer.alloc(4*1024*1024+1), "image/png"], [Buffer.from("pdf"), "application/pdf"]])("rejects oversized or unsupported inputs", async (bytes, mime) => {
    await expect(imageSources(client(bytes as Buffer, mime as string) as unknown as ChatEdgeClient, "c", [id], signal)).rejects.toThrow();
  });
  it("rejects URLs and excessive references", async () => {
    const c = client() as unknown as ChatEdgeClient;
    await expect(imageSources(c, "c", ["https://example.com"], signal)).rejects.toThrow("IDs");
    await expect(imageSources(c, "c", [id,id,id,id,id], signal)).rejects.toThrow("IDs");
  });
  it("stops before download on cancellation", async () => {
    const c = client(); const cancel = new AbortController(); cancel.abort();
    await expect(imageSources(c as unknown as ChatEdgeClient, "c", [id], cancel.signal)).rejects.toThrow("cancelled");
    expect(c.downloadFileBytes).not.toHaveBeenCalled();
  });
});
