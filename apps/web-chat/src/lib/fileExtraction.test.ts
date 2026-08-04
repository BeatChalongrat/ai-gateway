// Extraction-watcher tests (B2).
//
// POST /files no longer blocks on text extraction, so the chip's final
// state arrives by polling GET /conversations/{id}/files. What matters
// here: watchers stop on a terminal status, several files share ONE
// request per interval, unsubscribing kills the loop, and a broken
// endpoint can't spin forever.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { listFiles: vi.fn() },
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

import type { FileAttachment } from "@/lib/types";
import {
  EXTRACTION_POLL_INTERVAL_MS,
  __resetExtractionWatchers,
  isExtracting,
  watchExtraction,
} from "./fileExtraction";

const CONV = "conv-1";

function row(
  id: string,
  status: FileAttachment["extraction_status"],
): FileAttachment {
  return {
    id,
    filename: `${id}.pdf`,
    content_type: "application/pdf",
    file_size_bytes: 10,
    extraction_status: status,
    created_at: "2026-08-03T00:00:00Z",
  };
}

/** Advance fake timers past one poll and drain the promise chain the
 *  tick() body creates (listFiles await + the follow-up schedule). */
async function advanceOnePoll() {
  await vi.advanceTimersByTimeAsync(EXTRACTION_POLL_INTERVAL_MS);
  await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  __resetExtractionWatchers();
});

afterEach(() => {
  __resetExtractionWatchers();
  vi.useRealTimers();
});

describe("isExtracting", () => {
  it("treats only pending/processing as in-flight", () => {
    expect(isExtracting("pending")).toBe(true);
    expect(isExtracting("processing")).toBe(true);
    expect(isExtracting("completed")).toBe(false);
    expect(isExtracting("failed")).toBe(false);
    expect(isExtracting("unsupported")).toBe(false);
    expect(isExtracting(undefined)).toBe(false);
  });
});

describe("watchExtraction", () => {
  it("does not poll at all for an already-terminal file", async () => {
    const onStatus = vi.fn();
    watchExtraction(CONV, "f1", "completed", onStatus);
    await advanceOnePoll();
    expect(mockApi.listFiles).not.toHaveBeenCalled();
    expect(onStatus).not.toHaveBeenCalled();
  });

  it("reports the terminal status and then stops polling", async () => {
    mockApi.listFiles.mockResolvedValue({ files: [row("f1", "completed")] });
    const onStatus = vi.fn();
    watchExtraction(CONV, "f1", "processing", onStatus);

    await advanceOnePoll();
    expect(onStatus).toHaveBeenCalledWith("completed");
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1);

    // Watcher retired — no further traffic.
    await advanceOnePoll();
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1);
  });

  it("keeps polling while the server is still processing", async () => {
    mockApi.listFiles.mockResolvedValue({ files: [row("f1", "processing")] });
    const onStatus = vi.fn();
    watchExtraction(CONV, "f1", "processing", onStatus);

    await advanceOnePoll();
    await advanceOnePoll();
    expect(mockApi.listFiles).toHaveBeenCalledTimes(2);
    expect(onStatus).toHaveBeenLastCalledWith("processing");
  });

  it("coalesces several files in one conversation into a single request per interval", async () => {
    mockApi.listFiles.mockResolvedValue({
      files: [row("f1", "processing"), row("f2", "completed")],
    });
    const a = vi.fn();
    const b = vi.fn();
    watchExtraction(CONV, "f1", "processing", a);
    watchExtraction(CONV, "f2", "processing", b);

    await advanceOnePoll();
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1);
    expect(a).toHaveBeenCalledWith("processing");
    expect(b).toHaveBeenCalledWith("completed");
  });

  it("stops when the last watcher unsubscribes", async () => {
    mockApi.listFiles.mockResolvedValue({ files: [row("f1", "processing")] });
    const stop = watchExtraction(CONV, "f1", "processing", vi.fn());
    stop();
    await advanceOnePoll();
    expect(mockApi.listFiles).not.toHaveBeenCalled();
  });

  it("retires a watcher whose file disappeared (deleted mid-extraction)", async () => {
    mockApi.listFiles.mockResolvedValue({ files: [] });
    const onStatus = vi.fn();
    watchExtraction(CONV, "f1", "processing", onStatus);

    await advanceOnePoll();
    expect(onStatus).not.toHaveBeenCalled();
    await advanceOnePoll();
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1);
  });

  it("gives up after repeated request failures instead of retrying forever", async () => {
    mockApi.listFiles.mockRejectedValue(new Error("boom"));
    watchExtraction(CONV, "f1", "processing", vi.fn());

    for (let i = 0; i < 10; i++) await advanceOnePoll();

    // MAX_CONSECUTIVE_ERRORS is 5; the loop must be dead well before 10.
    expect(mockApi.listFiles).toHaveBeenCalledTimes(5);
  });
});
