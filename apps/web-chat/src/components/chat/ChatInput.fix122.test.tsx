// FIX122 Part C — the sent message must not stay in (or come back to) the
// composer, and a send that fails must give its text back.

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    uploadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

// The composer's voice hooks reach for getUserMedia / speech APIs that
// jsdom doesn't have; stub them to their "unsupported" shape.
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    supported: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({
    isTranscribing: false,
    isRecording: false,
    start: vi.fn(),
    stop: vi.fn(),
    duration: 0,
  }),
}));

import { clearDraftStore, loadDraft, saveDraft } from "@/lib/draftStore";
import { clearOutbox, outboxAccepted, outboxGiveBack, outboxIsUndelivered, outboxPeek, outboxPut } from "@/lib/sendOutbox";
import { useI18nStore } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { ChatInput } from "./ChatInput";

const chatInitial = useChatStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  clearDraftStore();
  clearOutbox();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...chatInitial }, true);
  useModelStore.setState({ models: [] });
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.stubGlobal(
    "requestAnimationFrame",
    (cb: FrameRequestCallback): number => {
      cb(0);
      return 0;
    },
  );
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  clearDraftStore();
});


/** The conversation page's shape: an empty conversation renders the composer
 *  inside the centred hero; the first send puts rows in the store IN THE SAME
 *  TICK and the page swaps to the thread layout — a different place in the
 *  tree, so the composer is unmounted and a new one mounted. */
function Page({ onSent }: { onSent: (t: string) => void }) {
  const [empty, setEmpty] = useState(true);
  const composer = (
    <ChatInput
      conversationId="c1"
      disabled={false}
      streaming={false}
      activeModelId={AUTO_MODEL_ID}
      onSend={(t) => {
        onSent(t);
        setEmpty(false);
      }}
      onStop={vi.fn()}
    />
  );
  return empty ? <section data-testid="hero"><div>{composer}</div></section> : <main data-testid="thread">{composer}</main>;
}

const box = (c: HTMLElement) => c.querySelector("textarea") as HTMLTextAreaElement;

describe("FIX122 Part C — the composer is empty after a send", () => {
  it("first send in an empty conversation: the remounted composer does not get the sent text back", () => {
    const sent: string[] = [];
    const view = render(<Page onSent={(t) => sent.push(t)} />);
    fireEvent.change(box(view.container), { target: { value: "คำถามแรกของแชตนี้" } });
    fireEvent.keyDown(box(view.container), { key: "Enter" });

    expect(sent).toEqual(["คำถามแรกของแชตนี้"]);
    expect(view.getByTestId("thread")).toBeTruthy();
    expect(box(view.container).value).toBe("");
    expect(loadDraft("c1")).toBeNull();

    // and Enter again asks nothing
    fireEvent.keyDown(box(view.container), { key: "Enter" });
    expect(sent).toHaveLength(1);
  });

  it("an unsent draft still survives the same remount", () => {
    const view = render(<Page onSent={() => {}} />);
    fireEvent.change(box(view.container), { target: { value: "ยังพิมพ์ไม่เสร็จ" } });
    view.unmount();
    expect(loadDraft("c1")).toBe("ยังพิมพ์ไม่เสร็จ");
  });

  it("a failed send comes back into an empty composer", () => {
    const view = render(<Page onSent={() => {}} />);
    act(() => {
      useChatStore.setState({ returnedDrafts: { c1: { id: "r1", text: "ข้อความที่ส่งไม่ถึง" } } });
    });
    expect(box(view.container).value).toBe("ข้อความที่ส่งไม่ถึง");
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
  });

  it("…but never over what the person has typed since", () => {
    const view = render(<Page onSent={() => {}} />);
    fireEvent.change(box(view.container), { target: { value: "ข้อความใหม่" } });
    act(() => {
      useChatStore.setState({ returnedDrafts: { c1: { id: "r1", text: "ข้อความที่ส่งไม่ถึง" } } });
    });
    expect(box(view.container).value).toBe("ข้อความใหม่");
    expect(useChatStore.getState().returnedDrafts.c1).toBeUndefined();
  });
});

describe("FIX122 Part C — the send outbox", () => {
  it("holds the text from before the request until the server takes it", () => {
    outboxPut("c1", "req-1", "สวัสดี");
    expect(outboxPeek("c1")).toMatchObject({ requestId: "req-1", text: "สวัสดี" });
    outboxAccepted("c1", "another-send");
    expect(outboxPeek("c1")).not.toBeNull();
    outboxAccepted("c1", "req-1");
    expect(outboxPeek("c1")).toBeNull();
  });

  it("a failed send moves to the draft and is handed back once", () => {
    outboxPut("c1", "req-1", "สวัสดี");
    expect(outboxGiveBack("c1", "req-2")).toBeNull();
    expect(outboxGiveBack("c1", "req-1")).toBe("สวัสดี");
    expect(loadDraft("c1")).toBe("สวัสดี");
    expect(outboxPeek("c1")).toBeNull();
    expect(outboxGiveBack("c1", "req-1")).toBeNull();
  });

  it("a draft written since is not replaced", () => {
    outboxPut("c1", "req-1", "เก่า");
    saveDraft("c1", "ใหม่");
    expect(outboxGiveBack("c1", "req-1")).toBeNull();
    expect(loadDraft("c1")).toBe("ใหม่");
  });

  it("a files-only send records nothing", () => {
    outboxPut("c1", "req-1", "   ");
    expect(outboxPeek("c1")).toBeNull();
  });

  it("after a reload: delivered when the server has the run id or the text", () => {
    const entry = { requestId: "req-1", text: "สวัสดี", at: Date.now() };
    expect(outboxIsUndelivered(entry, [])).toBe(true);
    expect(outboxIsUndelivered(entry, [{ role: "assistant", content: "สวัสดี" }])).toBe(true);
    expect(outboxIsUndelivered(entry, [{ role: "user", content: "อย่างอื่น", run_id: "req-1" }])).toBe(false);
    expect(outboxIsUndelivered(entry, [{ role: "user", content: " สวัสดี " }])).toBe(false);
  });
});
