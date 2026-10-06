// AM5 phase A — the agent workspace is the chat's surface, not a copy of it.
//
// Side by side, the workspace's composer, message bubbles, file chips, failed
// turn and "working" indicator were separate implementations of the chat's,
// and looked it. Each test renders the chat's component and the workspace's
// and compares what a person sees: the same elements with the same classes,
// because they are the same component. AM11: the workspace follows the app's
// language, so the shared components read as they do in the chat — the tests
// here read both in Thai, and one reads the composer in English.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { upload } = vi.hoisted(() => ({ upload: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn(), uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("../api", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api")>();
  return { ...m, amApi: { ...m.amApi, upload } };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));
vi.mock("@/hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({ supported: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));
vi.mock("@/hooks/useWhisperInput", () => ({
  useWhisperInput: () => ({ isTranscribing: false, isRecording: false, start: vi.fn(), stop: vi.fn(), duration: 0 }),
}));

import { ChatInput } from "@/components/chat/ChatInput";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { useI18nStore } from "@/lib/i18n";
import type { Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import type { ProgressPayload, ThreadItem } from "../types";
import { Composer } from "./Composer";
import { ProgressCard } from "./ProgressCard";
import { ThreadItems } from "./ThreadView";

const chatInitial = useChatStore.getState();

beforeEach(() => {
  upload.mockReset();
  upload.mockImplementation(() => new Promise(() => {})); // stays "uploading"
  useI18nStore.setState({ locale: "th" });
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
});
afterEach(cleanup);

/** What the composer looks like: the classes of its root, pill, text box, attach and send. */
function composerLook(root: HTMLElement) {
  const textarea = root.querySelector("textarea") as HTMLTextAreaElement;
  const pill = textarea.parentElement!.parentElement as HTMLElement;
  const send = [...root.querySelectorAll("button")].find((b) => b.className.includes("rounded-full") && b.querySelector("svg path[d^='M8 14V2']")) as HTMLElement;
  const attach = within(root).getByTestId("attach-button");
  return {
    root: root.className,
    pill: pill.className,
    pillRadius: pill.style.borderRadius,
    textarea: textarea.className,
    shortcut: textarea.getAttribute("data-shortcut"),
    attach: attach.className,
    send: send.className,
    sendLabel: send.getAttribute("aria-label"),
  };
}

function item(kind: string, payload: unknown, id = "it-1"): ThreadItem {
  return { id, kind, rev: 1, created_at: "2026-09-28T10:00:00Z", payload } as unknown as ThreadItem;
}

function msg(partial: Partial<Message>): Message {
  return { id: "m1", role: "assistant", content: "", created_at: new Date().toISOString(), status: "completed", ...partial } as Message;
}

describe("AM5 — the workspace composer is the chat's composer", () => {
  it("root, pill, text box, attach and send are the same elements with the same classes", () => {
    const chat = render(
      <ChatInput conversationId="c1" disabled={false} streaming={false} activeModelId={AUTO_MODEL_ID} onSend={vi.fn()} onStop={vi.fn()} />,
    );
    const chatLook = composerLook(within(chat.container).getByTestId("composer-root"));
    chat.unmount();

    const agent = render(<Composer installId="i-1" onSent={vi.fn()} />);
    const agentLook = composerLook(within(agent.container).getByTestId("composer-root"));
    expect(agentLook).toEqual(chatLook);
  });

  // AM11 — the rule this replaces (AM4): "stays Thai inside an English app".
  it("follows the app's language: Thai in a Thai app, English in an English one", () => {
    const thai = render(<Composer installId="i-1" onSent={vi.fn()} />);
    expect(screen.getByTestId("am-composer-send").getAttribute("aria-label")).toBe("ส่ง");
    thai.unmount();
    useI18nStore.setState({ locale: "en" });
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    expect(screen.getByTestId("am-composer-send").getAttribute("aria-label")).toBe("Send");
    expect(screen.getByTestId("am-composer-input").getAttribute("placeholder")).toBe("Message the agent…");
  });

  it("a staged file is the chat's file card (progress while it uploads, remove on it)", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".pdf"]} />);
    fireEvent.change(screen.getByTestId("am-composer-file"), { target: { files: [new File(["x"], "IV6909-0015.pdf")] } });
    const chip = screen.getByTestId("am-staged-chip");
    expect(chip.getAttribute("data-status")).toBe("uploading");
    expect(within(chip).getByTestId("upload-progress")).toBeTruthy();
    expect(screen.getByTestId("composer-chip-row")).toBeTruthy();
  });
});

describe("AM5 — the workspace thread is the chat's thread", () => {
  it("the person's message is the chat's bubble, with copy on it", () => {
    const chat = render(<MessageBubble convId="c1" message={msg({ role: "user", content: "ปิดงบ ก.ย. 69" })} />);
    const chatPill = chat.getByText("ปิดงบ ก.ย. 69").closest("div") as HTMLElement;
    const chatRow = chatPill.parentElement!.parentElement as HTMLElement;
    const want = { pill: chatPill.className, radius: chatPill.style.borderRadius, row: chatRow.className, text: chat.getByText("ปิดงบ ก.ย. 69").className };
    chat.unmount();

    const agent = render(<ThreadItems items={[item("user_text", { text: "ปิดงบ ก.ย. 69" })]} />);
    const pill = agent.getByText("ปิดงบ ก.ย. 69").closest("div") as HTMLElement;
    const row = pill.parentElement!.parentElement as HTMLElement;
    expect({ pill: pill.className, radius: pill.style.borderRadius, row: row.className, text: agent.getByText("ปิดงบ ก.ย. 69").className }).toEqual(want);
    expect(within(row).getByRole("button", { name: "คัดลอกข้อความ" })).toBeTruthy();
  });

  it("a sent file is the chat's attachment card", () => {
    const chat = render(
      <MessageBubble
        convId="c1"
        message={msg({
          role: "user",
          content: "",
          files: [{ id: "f1", filename: "GL.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_size_bytes: 2048, created_at: "2026-09-28T10:00:00Z" }],
        } as Partial<Message>)}
      />,
    );
    const chatCard = chat.getByText("GL.xlsx").closest("div.group") as HTMLElement;
    const want = chatCard.className;
    chat.unmount();

    const agent = render(<ThreadItems items={[item("user_files", { files: [{ id: "f1", name: "GL.xlsx", size: 2048 }] })]} />);
    const card = agent.getByText("GL.xlsx").closest("div.group") as HTMLElement;
    expect(card.className).toBe(want);
  });

  it("a failed turn is the chat's failed-turn card", () => {
    const chat = render(<MessageBubble convId="c1" message={msg({ status: "error", content: "", error_message: "ทำรอบนี้ไม่สำเร็จ" })} />);
    const want = chat.getByRole("alert").className;
    chat.unmount();

    const agent = render(<ThreadItems items={[item("agent_text", { text: "ทำรอบนี้ไม่สำเร็จ", tone: "error" })]} />);
    expect(agent.getByRole("alert").className).toBe(want);
  });

  it("the agent's words are the chat's answer body", () => {
    const chat = render(<MessageBubble convId="c1" message={msg({ content: "สรุป **กำไร**" })} />);
    const want = (chat.getByTestId("assistant-body").parentElement as HTMLElement).className;
    chat.unmount();

    const agent = render(<ThreadItems items={[item("agent_text", { text: "สรุป **กำไร**" })]} />);
    expect((agent.getByTestId("am-agent-text") as HTMLElement).className).toBe(want);
  });

  it("a working round shows the chat's working indicator, not a spinner of its own", () => {
    const progress: ProgressPayload = { run_id: "run-1", kind: "run", status: "running", steps: [{ key: "compute", label: "คำนวณด้วยสคริปต์", status: "running" }] };
    render(<ProgressCard progress={progress} />);
    const card = screen.getByTestId("am-progress");
    expect(card.querySelector(".cat-indicator")).toBeTruthy();
    expect(card.querySelector(".cat-indicator .shimmer-text")?.textContent).toBe("กำลังทำงาน");
  });
});
