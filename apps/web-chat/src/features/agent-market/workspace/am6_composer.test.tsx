// AM6 part D2 — the workspace composer is the chat's composer, with its
// affordances decided one by one.
//
//   keep   attach, dictation, the model control, send
//   add    Knowledge — in ask mode only (no files staged); it answers
//          questions and is never the source of a number in a deliverable
//   leave  folders and projects
//   and    a short placeholder: the examples it used to spell out are chips
//          above the composer, which go once a round is underway
//
// Before: no microphone, no knowledge chip, and a placeholder that read
// "แนบไฟล์ด้วยปุ่ม + หรือพิมพ์ เช่น ถามเกี่ยวกับตัวเลขของรอบล่าสุด หรือ “ทำรอบใหม่”" —
// a manual inside a text field, gone the moment someone starts typing.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, upload, mockApi, voice, whisper, hosted } = vi.hoisted(() => ({
  raw: vi.fn(),
  upload: vi.fn(),
  mockApi: { getKnowledgeAvailability: vi.fn(), listKnowledgeBases: vi.fn() },
  voice: { isSupported: true, start: vi.fn(), stop: vi.fn() },
  whisper: { isSupported: false, isTranscribing: false, start: vi.fn(), stop: vi.fn() },
  hosted: { now: false },
}));
vi.mock("@/lib/api", () => ({
  api: { raw, uploadFile: vi.fn(), fileObjectUrl: vi.fn(), downloadFile: vi.fn(), ...mockApi },
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
  usePathname: () => "/chat/agents/i/i-1",
}));
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => hosted.now }));
vi.mock("@/hooks/useVoiceOutput", () => ({ useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }) }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => voice }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => whisper }));

import { ChatInput } from "@/components/chat/ChatInput";
import { KBSelector } from "@/components/chat/KBSelector";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Button } from "@/components/ui/Button";
import { useI18nStore } from "@/lib/i18n";
import type { KnowledgeBase, Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";
import { useVoiceStore } from "@/stores/voiceStore";

import { amDictionaries } from "../i18n";
import type { Manifest, ThreadItem } from "../types";
import { resetAgentMarketForTest } from "../useAgentMarket";
import { Thai } from "../ui";
import { Composer } from "./Composer";
import { hasFinishedRound, roundUnderway } from "./rounds";
import { ThreadItems } from "./ThreadView";
import { WorkspacePage } from "./WorkspacePage";

const OLD_PLACEHOLDER = "แนบไฟล์ด้วยปุ่ม + หรือพิมพ์ เช่น ถามเกี่ยวกับตัวเลขของรอบล่าสุด หรือ “ทำรอบใหม่”";
const ASK_ONLY = "ฐานความรู้ใช้ตอบคำถามเท่านั้น ไม่ใช้กับตัวเลขในเอกสาร";
const SUGGEST_ATTACH = "แนบไฟล์เพื่อเริ่มรอบใหม่";
const SUGGEST_ASK = "ถามเกี่ยวกับตัวเลขของรอบล่าสุด";
const SUGGEST_FORMATS = "ขอไฟล์แบบอื่นของรอบล่าสุด";

const kb = (id: string, name: string): KnowledgeBase => ({
  id, name, description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50, is_active: true,
  document_count: 3, total_chunks: 30, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
});
const KBS = [kb("kb1", "ผังบัญชีและนโยบาย"), kb("kb2", "ศูนย์ต้นทุน")];

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const sent = () =>
  raw.mock.calls
    .filter((c) => c[0] === "/agent-market/installs/i-1/messages")
    .map((c) => JSON.parse(String((c[1] as RequestInit).body)) as Record<string, unknown>);

const chatInitial = useChatStore.getState();
const voiceInitial = useVoiceStore.getState();

beforeEach(async () => {
  resetAgentMarketForTest();
  raw.mockReset();
  upload.mockReset();
  upload.mockImplementation(async (_installId: string, f: File) => ({ id: `f-${f.name}`, name: f.name, size: f.size }));
  handler = (path) =>
    path === "/agent-market/installs/i-1/messages"
      ? json({ item: { id: "m-1", install_id: "i-1", kind: "user_text", payload: { text: "" }, rev: 9, created_at: new Date().toISOString() } })
      : json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true }, admin: false, publisher: false });
    if (path.includes("/events")) return new Promise(() => {});
    return handler(path, init);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  for (const f of [voice.start, voice.stop, whisper.start, whisper.stop]) f.mockReset();
  voice.isSupported = true;
  whisper.isTranscribing = false;
  hosted.now = false;
  mockApi.getKnowledgeAvailability.mockReset();
  mockApi.getKnowledgeAvailability.mockResolvedValue({ available: true });
  mockApi.listKnowledgeBases.mockReset();
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: KBS });
  useI18nStore.setState({ locale: "en" }); // the owner's app runs in English; the workspace is Thai
  useChatStore.setState({ ...chatInitial }, true);
  useVoiceStore.setState({ ...voiceInitial, inputMethod: "web-speech", isRecording: false, transcript: "", interimTranscript: "", error: null }, true);
  useModelStore.setState({ models: [] });
  window.localStorage.clear();
  await useKnowledgeStore.getState().loadAvailability(true);
  useKnowledgeStore.setState({ knowledgeBases: KBS, loaded: true, loading: false });
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const box = () => screen.getByTestId("am-composer-input") as HTMLTextAreaElement;
const send = () => screen.getByTestId("am-composer-send") as HTMLButtonElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const attach = (...names: string[]) =>
  fireEvent.change(screen.getByTestId("am-composer-file"), { target: { files: names.map((n) => new File(["x"], n)) } });
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

/** The chat's composer, read in Thai as the workspace reads it. */
function chatComposer(conversationId: string | null = "c1") {
  return render(
    <Thai>
      <ChatInput
        conversationId={conversationId}
        disabled={false}
        streaming={false}
        activeModelId={AUTO_MODEL_ID}
        activeKBId={null}
        onKBPick={vi.fn(async () => {})}
        onSend={vi.fn()}
        onStop={vi.fn()}
      />
    </Thai>,
  );
}

const model = <span data-testid="the-model-control" />;

// ─────────────────────────── dictation ───────────────────────────

describe("AM6 — dictation in the workspace is the chat's", () => {
  it("the microphone is the chat's button, between the model control and send", () => {
    const chat = chatComposer();
    const chatMic = chat.getByRole("button", { name: "ป้อนเสียง" });
    const want = chatMic.outerHTML;
    expect(chatMic.nextElementSibling?.getAttribute("aria-label")).toBe("ส่ง");
    chat.unmount();

    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    const mic = screen.getByRole("button", { name: "ป้อนเสียง" });
    expect(mic.outerHTML).toBe(want);
    expect(mic.previousElementSibling).toBe(screen.getByTestId("the-model-control"));
    expect(mic.nextElementSibling).toBe(send());
  });

  it("is not offered where the browser cannot record, as in the chat", () => {
    voice.isSupported = false;
    const chat = chatComposer();
    expect(chat.queryByRole("button", { name: "ป้อนเสียง" })).toBeNull();
    chat.unmount();
    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    expect(screen.queryByRole("button", { name: "ป้อนเสียง" })).toBeNull();
    expect(send()).toBeTruthy();
  });

  it("starts recording; the chat's recording bar takes the pill's place", () => {
    const chat = chatComposer();
    act(() => useVoiceStore.setState({ isRecording: true, interimTranscript: "ทำไมค่าที่ปรึกษา" }));
    const want = chat.getByRole("status").outerHTML;
    chat.unmount();
    act(() => useVoiceStore.setState({ isRecording: false, interimTranscript: "" }));

    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    fireEvent.click(screen.getByRole("button", { name: "ป้อนเสียง" }));
    expect(voice.start).toHaveBeenCalledTimes(1);
    act(() => useVoiceStore.setState({ isRecording: true, interimTranscript: "ทำไมค่าที่ปรึกษา" }));
    const bar = within(screen.getByTestId("composer-root")).getByRole("status");
    expect(bar.outerHTML).toBe(want);
    expect(bar.textContent).toContain("กำลังอัดเสียง");
    expect(bar.textContent).toContain("ทำไมค่าที่ปรึกษา");
    const pill = box().parentElement!.parentElement as HTMLElement;
    expect(pill.style.display).toBe("none");
    fireEvent.click(within(bar).getByRole("button"));
    expect(voice.stop).toHaveBeenCalledTimes(1);
  });

  it("what was said lands in the box, after what was typed, and can be sent", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    type("ช่วยดู");
    act(() => useVoiceStore.setState({ isRecording: true }));
    act(() => useVoiceStore.setState({ isRecording: false, transcript: "ทำไมค่าที่ปรึกษาสูงขึ้น" }));
    expect(box().value).toBe("ช่วยดู ทำไมค่าที่ปรึกษาสูงขึ้น");
    expect(useVoiceStore.getState().transcript).toBe("");
    expect((box().parentElement!.parentElement as HTMLElement).style.display).toBe("");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect(sent()[0]!.text).toBe("ช่วยดู ทำไมค่าที่ปรึกษาสูงขึ้น");
  });
});

// ─────────────────────────── knowledge ───────────────────────────

const chip = () => screen.queryByRole("button", { name: /^(เลือกฐานความรู้|ฐานความรู้: .+|ฐานความรู้)$/ });

async function pickKnowledge(name: string) {
  fireEvent.click(chip()!);
  fireEvent.click(await screen.findByText(name));
  await flush();
}

describe("AM6 — Knowledge, in ask mode only", () => {
  it("the chip is the chat's knowledge chip, where the chat has it: before the model control", () => {
    const chat = render(
      <Thai>
        <KBSelector conversationId="c1" activeKBId={null} onPick={vi.fn(async () => {})} />
      </Thai>,
    );
    const want = (chat.container.firstElementChild as HTMLElement).outerHTML;
    chat.unmount();

    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    const mine = chip()!.closest("div.relative") as HTMLElement;
    expect(mine.outerHTML).toBe(want);
    expect(mine.nextElementSibling).toBe(screen.getByTestId("the-model-control"));
    expect(screen.queryByTestId("am-kb-ask-only")).toBeNull();
  });

  it("the choice is kept for this agent, and only for it", async () => {
    const first = render(<Composer installId="i-1" onSent={vi.fn()} />);
    await pickKnowledge("ผังบัญชีและนโยบาย");
    expect(screen.getByRole("button", { name: "ฐานความรู้: ผังบัญชีและนโยบาย" })).toBeTruthy();
    expect(window.localStorage.getItem("am.kb.i-1")).toBe("kb1");
    first.unmount();

    const again = render(<Composer installId="i-1" onSent={vi.fn()} />);
    expect(screen.getByRole("button", { name: "ฐานความรู้: ผังบัญชีและนโยบาย" })).toBeTruthy();
    again.unmount();

    render(<Composer installId="i-2" onSent={vi.fn()} />);
    expect(screen.getByRole("button", { name: "เลือกฐานความรู้" })).toBeTruthy();
    expect(window.localStorage.getItem("am.kb.i-2")).toBeNull();
  });

  it("taking it off forgets it", async () => {
    window.localStorage.setItem("am.kb.i-1", "kb2");
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "เอาฐานความรู้ออกจากแชทนี้" }));
    await flush();
    expect(screen.getByRole("button", { name: "เลือกฐานความรู้" })).toBeTruthy();
    expect(window.localStorage.getItem("am.kb.i-1")).toBeNull();
  });

  it("a question sent with a knowledge base chosen carries it", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} threadId="t-7" />);
    await pickKnowledge("ผังบัญชีและนโยบาย");
    type("ทำไมค่าที่ปรึกษาสูงขึ้น");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    const body = sent()[0]!;
    expect(body).toEqual({ text: "ทำไมค่าที่ปรึกษาสูงขึ้น", file_ids: [], client_id: body.client_id, thread_id: "t-7", knowledge_base_id: "kb1" });
    expect(typeof body.client_id).toBe("string");
    // the choice stays for the next question
    await waitFor(() => expect(box().value).toBe(""));
    expect(screen.getByRole("button", { name: "ฐานความรู้: ผังบัญชีและนโยบาย" })).toBeTruthy();
  });

  it("the workspace says a knowledge base the way the chat's chip does: ฐานความรู้ (one screen, one word)", () => {
    const words = Object.entries(amDictionaries.th)
      .filter(([k]) => k.startsWith("knowledge."))
      .map(([, v]) => v as string);
    expect(words.length).toBeGreaterThan(0);
    for (const w of words) {
      expect(w).toContain("ฐานความรู้");
      expect(w).not.toContain("คลังความรู้");
    }
  });

  it("with files staged: no chip, one quiet line, and the knowledge base is never sent", async () => {
    window.localStorage.setItem("am.kb.i-1", "kb1");
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx"]} footer={<p data-testid="the-footer">อ่านอย่างเดียว</p>} />);
    expect(chip()).toBeTruthy();
    attach("GL_2569_09.xlsx");
    expect(chip()).toBeNull();
    const line = screen.getByTestId("am-kb-ask-only");
    expect(line.textContent).toBe(ASK_ONLY);
    // in the footer area, under the pill, with the agent's own footer
    const pill = box().parentElement!.parentElement as HTMLElement;
    expect(pill.contains(line)).toBe(false);
    expect(pill.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(line.compareDocumentPosition(screen.getByTestId("the-footer")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    type("ปิดงบเดือนกันยายน");
    await waitFor(() => expect(send().disabled).toBe(false));
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    const body = sent()[0]!;
    expect(body).toEqual({ text: "ปิดงบเดือนกันยายน", file_ids: ["f-GL_2569_09.xlsx"], client_id: body.client_id, thread_id: null });
    expect("knowledge_base_id" in body).toBe(false);

    // the files went with the message: ask mode again, the same knowledge base
    await waitFor(() => expect(screen.queryByTestId("am-kb-ask-only")).toBeNull());
    expect(screen.getByRole("button", { name: "ฐานความรู้: ผังบัญชีและนโยบาย" })).toBeTruthy();
  });

  it("a file that failed to attach is still a file: not ask mode", () => {
    window.localStorage.setItem("am.kb.i-1", "kb1");
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx"]} />);
    attach("photo.png");
    expect(screen.getByTestId("am-staged-chip").getAttribute("data-status")).toBe("error");
    expect(chip()).toBeNull();
    expect(screen.getByTestId("am-kb-ask-only")).toBeTruthy();
  });

  it("no knowledge base chosen: the message says nothing about knowledge", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    type("ขอสรุปรอบล่าสุด");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect(Object.keys(sent()[0]!).sort()).toEqual(["client_id", "file_ids", "text", "thread_id"]);
  });

  it("a knowledge base that no longer exists is forgotten, not sent", async () => {
    window.localStorage.setItem("am.kb.i-1", "kb-deleted");
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    await flush();
    expect(screen.getByRole("button", { name: "เลือกฐานความรู้" })).toBeTruthy();
    expect(window.localStorage.getItem("am.kb.i-1")).toBeNull();
    type("ทำไมค่าที่ปรึกษาสูงขึ้น");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect("knowledge_base_id" in sent()[0]!).toBe(false);
  });

  it("where knowledge cannot work and none is chosen: no chip and no line about it", async () => {
    mockApi.getKnowledgeAvailability.mockResolvedValue({ available: false, reason: "models_unavailable" });
    await useKnowledgeStore.getState().loadAvailability(true);
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx"]} />);
    expect(chip()).toBeNull();
    attach("GL.xlsx");
    expect(screen.queryByTestId("am-kb-ask-only")).toBeNull();
  });

  it("the page works without storage", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation((key: string) => {
      if (key.startsWith("am.kb.")) throw new Error("storage is off");
      return null;
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((key: string) => {
      if (key.startsWith("am.kb.")) throw new Error("storage is off");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation((key: string) => {
      if (key.startsWith("am.kb.")) throw new Error("storage is off");
    });
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    await pickKnowledge("ศูนย์ต้นทุน");
    expect(screen.getByRole("button", { name: "ฐานความรู้: ศูนย์ต้นทุน" })).toBeTruthy();
    type("ศูนย์ต้นทุนของฝ่ายขายคือรหัสอะไร");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect(sent()[0]!.knowledge_base_id).toBe("kb2");
  });
});

// ─────────────────────────── an answer that used knowledge ───────────────────────────

function item(kind: string, payload: unknown, id = "it-1"): ThreadItem {
  return { id, install_id: "i-1", kind, rev: 1, created_at: "2026-09-28T10:00:00Z", payload } as unknown as ThreadItem;
}

describe("AM6 — an answer that used knowledge says so", () => {
  const knowledge = {
    base: "ผังบัญชีและนโยบาย",
    sources: [{ title: "นโยบายค่าใช้จ่าย.pdf", locator: "หน้า 3" }, { title: "ผังบัญชี.xlsx" }],
  };

  it("one line under the answer, then the sources as the chat lists them", () => {
    useI18nStore.setState({ locale: "th" });
    const chat = render(
      <MessageBubble
        convId="c1"
        message={
          {
            id: "m1",
            role: "assistant",
            content: "ค่าที่ปรึกษาอยู่ในหมวด 5300",
            created_at: new Date().toISOString(),
            status: "completed",
            context_sources: [
              { id: "s1", source_type: "knowledge_chunk", display_label: "นโยบายค่าใช้จ่าย.pdf", metadata: { filename: "นโยบายค่าใช้จ่าย.pdf", excerpt: "หน้า 3" }, created_at: "" },
            ],
          } as unknown as Message
        }
      />,
    );
    const want = {
      row: chat.getByTestId("kb-sources").className,
      title: (chat.getByTestId("kb-sources").firstElementChild as HTMLElement).outerHTML,
      chip: chat.getAllByTestId("kb-source-chip")[0]!.outerHTML,
    };
    chat.unmount();
    useI18nStore.setState({ locale: "en" });

    render(<ThreadItems items={[item("agent_text", { text: "ค่าที่ปรึกษาอยู่ในหมวด 5300", knowledge })]} />);
    const block = screen.getByTestId("am-agent-knowledge");
    expect(within(block).getByTestId("am-agent-knowledge-line").textContent).toBe("จากฐานความรู้ “ผังบัญชีและนโยบาย” · ไม่ใช่ส่วนหนึ่งของรายงาน");
    const row = within(block).getByTestId("kb-sources");
    const chips = within(row).getAllByTestId("kb-source-chip");
    expect(chips.map((c) => c.textContent)).toEqual(["นโยบายค่าใช้จ่าย.pdf", "ผังบัญชี.xlsx"]);
    expect({ row: row.className, title: (row.firstElementChild as HTMLElement).outerHTML, chip: chips[0]!.outerHTML }).toEqual(want);
    // under the answer's words
    const words = screen.getByTestId("am-agent-text");
    expect(words.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // where in the file, one click away
    fireEvent.click(chips[0]!);
    expect(within(block).getByTestId("kb-cite-popover").textContent).toContain("หน้า 3");
  });

  it("an answer that used none reads as it did", () => {
    const plain = render(<ThreadItems items={[item("agent_text", { text: "รอบนี้กำไรสุทธิ 1.2 ล้านบาท" })]} />);
    expect(screen.queryByTestId("am-agent-knowledge")).toBeNull();
    expect(screen.queryByTestId("kb-sources")).toBeNull();
    const want = plain.container.innerHTML;
    plain.unmount();
    // knowledge with nothing in it is no knowledge
    const empty = render(<ThreadItems items={[item("agent_text", { text: "รอบนี้กำไรสุทธิ 1.2 ล้านบาท", knowledge: null })]} />);
    expect(empty.container.innerHTML).toBe(want);
  });

  it("a knowledge base named without sources still says where the answer came from", () => {
    render(<ThreadItems items={[item("agent_text", { text: "ตามนโยบาย เบิกได้ไม่เกิน 500 บาท", knowledge: { base: "ผังบัญชีและนโยบาย", sources: [] } })]} />);
    expect(screen.getByTestId("am-agent-knowledge-line").textContent).toBe("จากฐานความรู้ “ผังบัญชีและนโยบาย” · ไม่ใช่ส่วนหนึ่งของรายงาน");
    expect(screen.queryByTestId("kb-sources")).toBeNull();
  });
});

// ─────────────────────────── folders and projects ───────────────────────────

describe("AM6 — no folders, no projects", () => {
  it("in the desktop app, where the chat's composer has both, the workspace composer has neither", () => {
    hosted.now = true;
    render(<Composer installId="i-1" onSent={vi.fn()} tools={model} />);
    const root = screen.getByTestId("composer-root");
    for (const id of ["folder-chip", "folder-chip-trigger", "permission-mode-selector", "project-chip"]) {
      expect(within(root).queryByTestId(id)).toBeNull();
    }
    // beside the "+": nothing
    const attachButton = within(root).getByTestId("attach-button");
    expect(attachButton.parentElement?.parentElement?.children).toHaveLength(1);
    // and the "+" opens the file picker, not the desktop's menu
    expect(attachButton.getAttribute("aria-haspopup")).toBeNull();
  });
});

// ─────────────────────────── the placeholder ───────────────────────────

describe("AM6 — the placeholder is short", () => {
  it("names the agent, like the chat's names the app", () => {
    const chat = chatComposer();
    expect((chat.container.querySelector("textarea") as HTMLTextAreaElement).placeholder).toBe("ส่งข้อความถึง SRAN Chat…");
    chat.unmount();

    render(<Composer installId="i-1" onSent={vi.fn()} agentName="ปิดงบ" />);
    expect(box().placeholder).toBe("ส่งข้อความถึง ปิดงบ…");
    expect(box().placeholder).not.toBe(OLD_PLACEHOLDER);
    expect(box().placeholder.length).toBeLessThan(30);
  });

  it("before the agent's name is known", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    expect(box().placeholder).toBe("ส่งข้อความถึง agent…");
  });

  it("says a file is going up, in the chat's words", () => {
    upload.mockImplementation(() => new Promise(() => {}));
    render(<Composer installId="i-1" onSent={vi.fn()} agentName="ปิดงบ" accept={[".xlsx"]} />);
    attach("GL.xlsx");
    expect(box().placeholder).toBe("กำลังอัปโหลด…");
  });
});

// ─────────────────────────── suggestions ───────────────────────────

const suggestions = () => screen.queryByTestId("am-composer-suggestions");
const suggestionTexts = () => within(suggestions()!).getAllByRole("button").map((b) => b.textContent);

describe("AM6 — what the placeholder used to explain is a chip to click", () => {
  it("before any round: attach, which opens the file picker like the +", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} />);
    expect(suggestionTexts()).toEqual([SUGGEST_ATTACH]);
    const picker = vi.spyOn(screen.getByTestId("am-composer-file") as HTMLInputElement, "click");
    fireEvent.click(screen.getByRole("button", { name: SUGGEST_ATTACH }));
    expect(picker).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("attach-button"));
    expect(picker).toHaveBeenCalledTimes(2);
  });

  it("after a finished round: three, and asking puts a sentence in the box to edit before sending", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} finishedRound />);
    expect(suggestionTexts()).toEqual([SUGGEST_ATTACH, SUGGEST_ASK, SUGGEST_FORMATS]);
    fireEvent.click(screen.getByRole("button", { name: SUGGEST_ASK }));
    expect(box().value).toBe("ช่วยอธิบายตัวเลขของรอบล่าสุด");
    expect(document.activeElement).toBe(box());
    expect(raw).not.toHaveBeenCalled();
    // text is typed: the chips step aside, and come back with an empty box
    expect(suggestions()).toBeNull();
    type("");
    expect(suggestionTexts()).toEqual([SUGGEST_ATTACH, SUGGEST_ASK, SUGGEST_FORMATS]);
    fireEvent.click(screen.getByRole("button", { name: SUGGEST_FORMATS }));
    expect(box().value).toBe("ขอไฟล์แบบอื่นของรอบล่าสุด");
    expect(document.activeElement).toBe(box());
  });

  it("they are the app's small secondary button, above the pill in the composer's column", () => {
    const app = render(
      <Button variant="secondary" size="sm">
        x
      </Button>,
    );
    const want = (app.container.querySelector("button") as HTMLElement).className;
    app.unmount();

    render(<Composer installId="i-1" onSent={vi.fn()} finishedRound />);
    for (const b of within(suggestions()!).getAllByRole("button")) {
      expect(b.className.replace(/\s+/g, " ").trim()).toBe(want.replace(/\s+/g, " ").trim());
      expect(b.getAttribute("type")).toBe("button");
    }
    const pill = box().parentElement!.parentElement as HTMLElement;
    expect(suggestions()!.parentElement).toBe(pill.parentElement);
    expect(suggestions()!.compareDocumentPosition(pill) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("gone once a round is underway", () => {
    const view = render(<Composer installId="i-1" onSent={vi.fn()} finishedRound roundUnderway />);
    expect(suggestions()).toBeNull();
    view.rerender(<Composer installId="i-1" onSent={vi.fn()} finishedRound roundUnderway={false} />);
    expect(suggestionTexts()).toHaveLength(3);
  });

  it("gone while files are staged, and while recording", () => {
    render(<Composer installId="i-1" onSent={vi.fn()} finishedRound accept={[".xlsx"]} />);
    attach("GL.xlsx");
    expect(suggestions()).toBeNull();
    cleanup();
    render(<Composer installId="i-1" onSent={vi.fn()} finishedRound />);
    act(() => useVoiceStore.setState({ isRecording: true }));
    expect(suggestions()).toBeNull();
  });
});

describe("AM6 — what counts as a round underway, and as a finished one", () => {
  const at = (s: number) => new Date(Date.UTC(2026, 8, 28, 3, 0, s)).toISOString();
  const progress = (status: string, kind = "run", run = "r-1"): ThreadItem =>
    ({ id: `p-${run}`, install_id: "i-1", kind: "progress", run_id: run, payload: { run_id: run, kind, status }, rev: 2, created_at: at(1) }) as ThreadItem;
  const outputs = (run = "r-1"): ThreadItem =>
    ({ id: `o-${run}`, install_id: "i-1", kind: "outputs", run_id: run, payload: { run_id: run, artifacts: [{ id: "a", name: "report.docx", format: "docx", size: 1, version: 1 }] }, rev: 3, created_at: at(2) }) as ThreadItem;
  const greeting = item("greeting", { text: "สวัสดีครับ" }, "g");

  it("queued, running or waiting for an answer is underway; finished is not", () => {
    expect(roundUnderway([greeting])).toBe(false);
    for (const s of ["queued", "running", "awaiting_input"]) expect(roundUnderway([greeting, progress(s)])).toBe(true);
    for (const s of ["succeeded", "failed", "cancelled"]) expect(roundUnderway([greeting, progress(s)])).toBe(false);
    // the agent answering a question, or making another file format, is work underway too
    expect(roundUnderway([greeting, progress("succeeded"), progress("running", "reply", "r-2")])).toBe(true);
  });

  it("a finished round is the person's latest round, ended, with files to ask about", () => {
    expect(hasFinishedRound([greeting])).toBe(false);
    expect(hasFinishedRound([greeting, progress("succeeded"), outputs()])).toBe(true);
    expect(hasFinishedRound([greeting, progress("running")])).toBe(false);
    // the sample's self-test is not the person's round
    expect(hasFinishedRound([greeting, progress("succeeded", "self_test"), outputs()])).toBe(false);
    // a round that failed and left nothing has no figures to explain
    expect(hasFinishedRound([greeting, progress("failed")])).toBe(false);
  });
});

// ─────────────────────────── the workspace ───────────────────────────

describe("AM6 — the workspace mounts it that way", () => {
  const manifest = {
    id: "sran.financial-report",
    name: "รายงานการเงินรายเดือน",
    short_name: "ปิดงบ",
    greeting: { text: "สวัสดีครับ", role: "ผู้ช่วยปิดงบการเงิน", tagline: "อ่านอย่างเดียว" },
    inputs: [{ role: "primary", label: "บัญชีแยกประเภท", required: true, accept: [".xlsx"] }],
    outputs: [{ format: "docx", label: "รายงาน", default: true }],
  } as unknown as Manifest;
  const at = (s: number) => new Date(Date.UTC(2026, 8, 28, 3, 0, s)).toISOString();
  const round = (status: string): ThreadItem[] => [
    { id: "g", install_id: "i-1", kind: "greeting", payload: { text: "สวัสดีครับ" }, rev: 1, created_at: at(0) },
    { id: "p", install_id: "i-1", kind: "progress", run_id: "r-1", payload: { run_id: "r-1", kind: "run", status }, rev: 2, created_at: at(1) },
    ...(status === "succeeded"
      ? [
          {
            id: "o",
            install_id: "i-1",
            kind: "outputs",
            run_id: "r-1",
            payload: { run_id: "r-1", period_label: "กันยายน 2569", artifacts: [{ id: "a1", name: "report_v1.docx", format: "docx", size: 10, version: 1 }], model: { uses: "text", model: "qwen3.8-27b-fp8", self_hosted: true } },
            rev: 3,
            created_at: at(2),
          } as ThreadItem,
        ]
      : []),
  ];
  const workspace = (items: ThreadItem[], m: Manifest = manifest): Handler => (path) => {
    if (path === "/agent-market/installs") return json({ installs: [{ id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน", created_at: "" }] });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน" }, manifest: m, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items, cursor: 99 });
    if (path === "/agent-market/installs/i-1/models")
      return json({ uses: "text", choice: "auto", auto_model: "", models: [], blocked_reason: "", hidden_reason: "", reads: "", note: "" });
    if (path === "/agent-market/installs/i-1/storage")
      return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    if (path === "/agent-market/installs/i-1/messages") return json({ item: { id: "m-1", install_id: "i-1", kind: "user_text", payload: { text: "x" }, rev: 100, created_at: at(9) } });
    return json({ ok: true });
  };

  it("a finished round: the agent's short name in the box, three chips above it, the model control, the microphone", async () => {
    handler = workspace(round("succeeded"));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-round-divider");
    await waitFor(() => expect(box().placeholder).toBe("ส่งข้อความถึง ปิดงบ…"));
    expect(suggestionTexts()).toEqual([SUGGEST_ATTACH, SUGGEST_ASK, SUGGEST_FORMATS]);
    const controls = await screen.findByTestId("am-model-control");
    const mic = screen.getByRole("button", { name: "ป้อนเสียง" });
    expect(controls.compareDocumentPosition(mic) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mic.nextElementSibling).toBe(send());
  });

  it("a round underway: no chips", async () => {
    handler = workspace(round("running"));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-progress");
    expect(suggestions()).toBeNull();
    expect(box().placeholder.length).toBeLessThan(30);
  });

  it("an agent without a short name is called by its name", async () => {
    handler = workspace(round("succeeded"), { ...manifest, short_name: undefined } as Manifest);
    render(<WorkspacePage installId="i-1" />);
    await waitFor(() => expect(box().placeholder).toBe("ส่งข้อความถึง รายงานการเงินรายเดือน…"));
  });

  it("a message sent from the workspace names no model for one round", async () => {
    handler = workspace(round("succeeded"));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-round-divider");
    type("ขอสรุปรอบล่าสุด");
    fireEvent.click(send());
    await waitFor(() => expect(sent()).toHaveLength(1));
    expect(Object.keys(sent()[0]!).sort()).toEqual(["client_id", "file_ids", "text", "thread_id"]);
  });
});
