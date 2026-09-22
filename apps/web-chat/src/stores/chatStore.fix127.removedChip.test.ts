// FIX127 A1 — removing a chip removes the file.
//
// R3 (ddbb237d): a 30 MB file removed at 9 % was still uploaded, still
// stored, and still read by the model on every later turn, because the X
// on the chip was a pure array filter. Now the X aborts the transfer and
// takes the server row back — by id when the upload finished, by the
// client_id the page sent with the body when it did not, and a chip
// removed before its upload started is never uploaded at all.

import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    deleteFile: vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    deleteFileByClientId: vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    openMessageStream: vi.fn(),
    cancelGeneration: vi.fn(),
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
vi.mock("@/hooks/useUsage", () => ({ bumpUsageVersion: vi.fn() }));

import {
  _resetUploadRegistry,
  beginUpload,
  endUpload,
  LATE_DELETE_RETRY_MS,
} from "@/lib/uploadRegistry";
import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();
const CONV = "conv-1";

function file(name = "big.bin") {
  return new File(["0123456789"], name, { type: "application/octet-stream" });
}

describe("FIX127 A1 — removing a chip removes the file", () => {
  beforeEach(() => {
    useChatStore.setState(initialState, true);
    _resetUploadRegistry();
    mockApi.deleteFile.mockClear();
    mockApi.deleteFileByClientId.mockClear();
    vi.useFakeTimers();
  });

  it("aborts an upload in flight (9 %) and deletes by client_id, now and once more later", () => {
    const s = useChatStore.getState();
    const pid = s.addPendingFile(CONV, file());
    const ctrl = beginUpload(pid, CONV);
    expect(ctrl).not.toBeNull();
    s.updateFileProgress(CONV, pid, 9);

    s.removePendingFile(CONV, pid);

    expect(ctrl!.signal.aborted).toBe(true);
    expect(useChatStore.getState().pendingFiles[CONV] ?? []).toHaveLength(0);
    expect(mockApi.deleteFile).not.toHaveBeenCalled();
    expect(mockApi.deleteFileByClientId).toHaveBeenCalledTimes(1);
    expect(mockApi.deleteFileByClientId).toHaveBeenCalledWith(CONV, pid);
    vi.advanceTimersByTime(LATE_DELETE_RETRY_MS);
    expect(mockApi.deleteFileByClientId).toHaveBeenCalledTimes(2);
  });

  it("deletes by server id once the upload finished (100 % / ready)", () => {
    const s = useChatStore.getState();
    const pid = s.addPendingFile(CONV, file());
    const ctrl = beginUpload(pid, CONV)!;
    endUpload(pid);
    s.markFileReady(CONV, pid, "srv-42", "completed");

    s.removePendingFile(CONV, pid);

    expect(ctrl.signal.aborted).toBe(false); // nothing left to abort
    expect(mockApi.deleteFile).toHaveBeenCalledWith("srv-42");
    expect(mockApi.deleteFileByClientId).not.toHaveBeenCalled();
  });

  it("a chip removed before its upload started (0 %, queued) is never uploaded", () => {
    const s = useChatStore.getState();
    const pid = s.addPendingFile(CONV, file());

    s.removePendingFile(CONV, pid); // before beginUpload — still in the worker queue

    expect(mockApi.deleteFile).not.toHaveBeenCalled();
    expect(mockApi.deleteFileByClientId).not.toHaveBeenCalled();
    // The worker reaches it next: it must be told to skip.
    expect(beginUpload(pid, CONV)).toBeNull();
  });

  it("sending (clearPendingFiles) deletes nothing — those files belong to the message", () => {
    const s = useChatStore.getState();
    const pid = s.addPendingFile(CONV, file());
    beginUpload(pid, CONV);
    endUpload(pid);
    s.markFileReady(CONV, pid, "srv-7", "completed");

    s.clearPendingFiles(CONV);
    vi.advanceTimersByTime(LATE_DELETE_RETRY_MS * 2);

    expect(mockApi.deleteFile).not.toHaveBeenCalled();
    expect(mockApi.deleteFileByClientId).not.toHaveBeenCalled();
  });
});
