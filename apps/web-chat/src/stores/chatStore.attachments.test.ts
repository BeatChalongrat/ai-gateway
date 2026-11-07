// chatStore attachment tests (B1).
//
// Regression cover for "the AI clearly read my PDF, but my own message
// in the transcript shows no file chip — only the header says '1 file'".
//
// The file WAS linked to the message server-side; what broke was purely
// the optimistic bubble. ChatInput.submit() clears its pending-file
// bucket synchronously right after calling onSend, but the landing page
// wires onSend to an async handler that awaits ensureConv() before it
// reaches sendMessage. So sendMessage ran a turn of the microtask queue
// later, against an already-emptied bucket, and rendered the user
// message with `files: undefined`.
//
// These tests pin the ordering contract in both directions: metadata
// handed over via stageOutgoingFiles survives an async gap, and the
// legacy pendingFiles scan still covers callers that never staged.

import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    openMessageStream: vi.fn(),
    getConversation: vi.fn(),
    generateTitle: vi.fn(),
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

import type { FileAttachment } from "@/lib/types";
import { useChatStore } from "./chatStore";

const initialState = useChatStore.getState();

const CONV = "conv-1";
const DRAFT = "__chat_input_draft__";

function pdfFile(name = "transcript.pdf") {
  return new File(["%PDF-1.4"], name, { type: "application/pdf" });
}

function shell(id: string, name: string): FileAttachment {
  return {
    id,
    filename: name,
    content_type: "application/pdf",
    file_size_bytes: 8,
    extraction_status: "completed",
    created_at: "2026-08-03T00:00:00Z",
  };
}

/** The stream never opens in these tests — we only care about the
 *  optimistic state sendMessage sets before its first await. */
function stallTheStream() {
  mockApi.openMessageStream.mockImplementation(
    () => new Promise<Response>(() => {}),
  );
}

function userMessageFiles(convId: string) {
  const msgs = useChatStore.getState().messages[convId] ?? [];
  return msgs.find((m) => m.role === "user")?.files;
}

beforeEach(() => {
  useChatStore.setState({ ...initialState }, true);
  vi.clearAllMocks();
  useChatStore.setState({
    conversations: [
      {
        id: CONV,
        title: "T",
        model_id: "claude-sonnet-4",
        message_count: 0,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ],
    messages: { [CONV]: [] },
    messagesLoaded: { [CONV]: true },
  });
  stallTheStream();
});

describe("optimistic attachments on the user message", () => {
  it("renders the chip when the composer stages metadata and THEN clears its bucket before sendMessage runs (landing-page ordering)", async () => {
    const st = useChatStore.getState();
    // Composer staged the chip under the DRAFT bucket (landing page:
    // ChatInput's conversationId prop stays null for its lifetime).
    const pid = st.addPendingFile(DRAFT, pdfFile());
    st.markFileReady(DRAFT, pid, "file-1", "completed");

    // submit(): hand metadata over, call onSend, clear the bucket.
    st.stageOutgoingFiles([shell("file-1", "transcript.pdf")]);
    st.clearPendingFiles(DRAFT);

    // The landing page's onSend awaits ensureConv() first — that gap is
    // the whole bug. Reproduce it faithfully.
    await Promise.resolve();
    await Promise.resolve();

    void useChatStore.getState().sendMessage(CONV, "what is this?", ["file-1"]);

    const files = userMessageFiles(CONV);
    expect(files).toHaveLength(1);
    expect(files?.[0].id).toBe("file-1");
    expect(files?.[0].filename).toBe("transcript.pdf");
  });

  it("still resolves chips from pendingFiles when nothing was staged (conversation-page ordering / direct callers)", () => {
    const st = useChatStore.getState();
    const pid = st.addPendingFile(CONV, pdfFile("notes.pdf"));
    st.markFileReady(CONV, pid, "file-2", "completed");

    void useChatStore.getState().sendMessage(CONV, "summarise", ["file-2"]);

    const files = userMessageFiles(CONV);
    expect(files).toHaveLength(1);
    expect(files?.[0].id).toBe("file-2");
    expect(files?.[0].filename).toBe("notes.pdf");
  });

  it("keeps the caller's file order rather than bucket iteration order", () => {
    const st = useChatStore.getState();
    st.stageOutgoingFiles([
      shell("file-b", "b.pdf"),
      shell("file-a", "a.pdf"),
    ]);

    void useChatStore
      .getState()
      .sendMessage(CONV, "compare", ["file-a", "file-b"]);

    expect(userMessageFiles(CONV)?.map((f) => f.id)).toEqual([
      "file-a",
      "file-b",
    ]);
  });

  it("consumes staged metadata so a later send cannot resurrect it", async () => {
    const st = useChatStore.getState();
    st.stageOutgoingFiles([shell("file-1", "transcript.pdf")]);

    void useChatStore.getState().sendMessage(CONV, "first", ["file-1"]);
    expect(userMessageFiles(CONV)).toHaveLength(1);
    expect(useChatStore.getState().outgoingFiles["file-1"]).toBeUndefined();

    // Second turn, no file_ids: must not inherit the previous chip.
    useChatStore.setState({ messages: { [CONV]: [] } });
    void useChatStore.getState().sendMessage(CONV, "second");
    expect(userMessageFiles(CONV)).toBeUndefined();
  });

  it("carries a still-extracting status onto the optimistic chip", () => {
    const st = useChatStore.getState();
    const pid = st.addPendingFile(CONV, pdfFile("scan.pdf"));
    st.markFileReady(CONV, pid, "file-3", "processing");

    void useChatStore.getState().sendMessage(CONV, "read this", ["file-3"]);

    expect(userMessageFiles(CONV)?.[0].extraction_status).toBe("processing");
  });

  it("ignores staged rows the send did not reference", () => {
    const st = useChatStore.getState();
    st.stageOutgoingFiles([shell("file-1", "a.pdf"), shell("file-9", "b.pdf")]);

    void useChatStore.getState().sendMessage(CONV, "only a", ["file-1"]);

    expect(userMessageFiles(CONV)?.map((f) => f.id)).toEqual(["file-1"]);
  });
});

describe("pending-file extraction status", () => {
  it("markFileReady records the server's extraction_status", () => {
    const st = useChatStore.getState();
    const pid = st.addPendingFile(CONV, pdfFile());
    st.markFileReady(CONV, pid, "file-1", "processing");

    const row = useChatStore.getState().pendingFiles[CONV][0];
    expect(row.status).toBe("ready");
    expect(row.extractionStatus).toBe("processing");
  });

  it("setFileExtractionStatus advances the chip without touching upload status", () => {
    const st = useChatStore.getState();
    const pid = st.addPendingFile(CONV, pdfFile());
    st.markFileReady(CONV, pid, "file-1", "processing");
    useChatStore.getState().setFileExtractionStatus(CONV, pid, "completed");

    const row = useChatStore.getState().pendingFiles[CONV][0];
    expect(row.extractionStatus).toBe("completed");
    expect(row.status).toBe("ready");
    expect(row.serverId).toBe("file-1");
  });
});

describe("late extraction updates", () => {
  it("does not resurrect a bucket that submit() already cleared", () => {
    const st = useChatStore.getState();
    const pid = st.addPendingFile(DRAFT, pdfFile());
    st.markFileReady(DRAFT, pid, "file-1", "processing");
    st.clearPendingFiles(DRAFT);

    // Poll lands after the chips are gone.
    useChatStore.getState().setFileExtractionStatus(DRAFT, pid, "completed");

    expect(useChatStore.getState().pendingFiles[DRAFT]).toBeUndefined();
  });
});
