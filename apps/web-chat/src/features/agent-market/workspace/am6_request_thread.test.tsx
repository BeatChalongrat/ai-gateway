// AM6 — the files sent on a request-files card go to the thread the card is
// in. A reminder's round may be in a thread of its own (the install's first
// thread had a round open when it fell due): its files must reach that round,
// not the first thread's.
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

const message = vi.fn().mockResolvedValue({ item: null });
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, amApi: { ...real.amApi, message: (...a: unknown[]) => message(...a) } };
});

// the slot's files are already stored: what the card does next is what is tested
vi.mock("./useUploads", async (orig) => {
  const real = await orig<typeof import("./useUploads")>();
  return {
    ...real,
    useUploads: () => ({ items: [], ids: ["file-1", "file-2"], uploading: false, add: vi.fn(), remove: vi.fn(), retry: vi.fn(), clear: vi.fn() }),
  };
});

import { useI18nStore } from "@/lib/i18n";

import type { CardPayload } from "../types";
import { WorkspaceContext } from "./context";
import { RequestFilesCard } from "./RequestFilesCard";

const card: CardPayload = {
  card_id: "card-1",
  run_id: "run-1",
  kind: "request_files",
  status: "open",
  blocking: true,
  attempts: 0,
  questions: [
    {
      key: "files",
      code: "reminder_files",
      severity: "blocking",
      header: "ไฟล์ของรอบนี้",
      question: "ส่งไฟล์ของงวดนี้มาได้เลย",
      options: [],
      request: [{ role: "po", label: "ใบสั่งซื้อ", required: true, accept: [".xlsx"] }],
    },
  ],
};

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  message.mockClear();
});
afterEach(cleanup);

describe("files sent on a request-files card", () => {
  it("go to the thread the card is in", async () => {
    render(
      <WorkspaceContext.Provider value={{ installId: "inst-1", merge: vi.fn(), threadId: "thread-2" }}>
        <RequestFilesCard card={card} submit={vi.fn()} />
      </WorkspaceContext.Provider>,
    );
    await waitFor(() => expect(message).toHaveBeenCalledTimes(1));
    expect(message.mock.calls[0][0]).toBe("inst-1");
    expect(message.mock.calls[0][1]).toMatchObject({ file_ids: ["file-1", "file-2"], thread_id: "thread-2" });
  });

  it("the install's first thread is said as none", async () => {
    render(
      <WorkspaceContext.Provider value={{ installId: "inst-1", merge: vi.fn(), threadId: null }}>
        <RequestFilesCard card={card} submit={vi.fn()} />
      </WorkspaceContext.Provider>,
    );
    await waitFor(() => expect(message).toHaveBeenCalledTimes(1));
    expect(message.mock.calls[0][1]).toMatchObject({ thread_id: null });
  });
});
