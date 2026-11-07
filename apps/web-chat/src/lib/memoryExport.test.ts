// FIX84 row 5 — "Export ready" only after the bytes are in hand, and the
// bytes travel through the authenticated client, never a bare window.open.

import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { downloadMemoryExport, type MemoryExportClient } from "./memoryExport";

const meta = { export_id: "exp 1", filename: "memories.json", size_bytes: 42, expires_at: "2026-09-09T00:00:00Z" };

function client(fileResponse: Partial<Response>): MemoryExportClient & { raw: ReturnType<typeof vi.fn> } {
  return {
    exportMemories: vi.fn(async () => meta),
    raw: vi.fn(async () => ({ ok: true, status: 200, statusText: "OK", blob: async () => new Blob(["{}"]), ...fileResponse }) as Response),
  };
}

describe("downloadMemoryExport (FIX84 row 5)", () => {
  it("fetches the file through the authenticated client and delivers it as a download", async () => {
    const c = client({});
    const deliver = vi.fn();
    const out = await downloadMemoryExport(c, deliver);
    expect(c.raw).toHaveBeenCalledWith("/account/exports/exp%201/download");
    expect(deliver).toHaveBeenCalledTimes(1);
    expect(deliver.mock.calls[0][1]).toBe("memories.json");
    expect(out).toEqual({ filename: "memories.json", sizeBytes: 42 });
  });

  it("a 401 on the download (the desktop's cookie-less path) throws BEFORE anything is delivered — so the caller never says ready", async () => {
    const c = client({ ok: false, status: 401, statusText: "Unauthorized" });
    const deliver = vi.fn();
    await expect(downloadMemoryExport(c, deliver)).rejects.toBeInstanceOf(ApiError);
    expect(deliver).not.toHaveBeenCalled();
  });

  it("never opens a window (the FIX83 defect)", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await downloadMemoryExport(client({}), vi.fn());
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });
});
