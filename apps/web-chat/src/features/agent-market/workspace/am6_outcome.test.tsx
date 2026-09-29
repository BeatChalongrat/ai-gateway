// AM6 part F — the words match the billing (the workspace).
//
// A reconciliation that does not tie delivered the work: it read the files,
// matched what matched and said how much is unexplained. That is a result,
// not a failure, and it is charged like any round that delivers.
//
//   * Its message is a calm card headed "เสร็จแล้ว · ยังไม่ลงตัว" — not the
//     chat's failed-turn card, not an error colour, and with no "ลองใหม่ด้วย
//     ไฟล์เดิม" (the same files give the same result).
//   * A round that crashed keeps its card, and says that nothing was charged.
//   * The progress card, the round's divider and the files card go by the
//     run's outcome; a server that sends none is read by its status.
//   * What a round costs is said before it starts: on the empty workspace,
//     and above the composer while files are staged and no round is underway.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, upload, mockApi, voice, whisper } = vi.hoisted(() => ({
  raw: vi.fn(),
  upload: vi.fn(),
  mockApi: { getKnowledgeAvailability: vi.fn(), listKnowledgeBases: vi.fn() },
  voice: { isSupported: false, start: vi.fn(), stop: vi.fn() },
  whisper: { isSupported: false, isTranscribing: false, start: vi.fn(), stop: vi.fn() },
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
vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => false }));
vi.mock("@/hooks/useVoiceOutput", () => ({ useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }) }));
vi.mock("@/hooks/useVoiceInput", () => ({ useVoiceInput: () => voice }));
vi.mock("@/hooks/useWhisperInput", () => ({ useWhisperInput: () => whisper }));

import { useI18nStore } from "@/lib/i18n";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

import type { Completion, Failure, Manifest, OutputsPayload, ProgressPayload, ThreadItem } from "../types";
import { resetAgentMarketForTest } from "../useAgentMarket";
import { WorkspaceContext, type WorkspaceCtx } from "./context";
import { OutputsCard } from "./OutputsCard";
import { ProgressCard } from "./ProgressCard";
import { ThreadItems, ThreadView } from "./ThreadView";
import { WorkspacePage } from "./WorkspacePage";

const RUN = "85b347e0-7dfd-4dff-9418-8c48c85f494d";
const OPEN_TITLE = "เสร็จแล้ว · ยังไม่ลงตัว";
const OPEN_OTHER = "เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent";
const RETRY = "ลองใหม่ด้วยไฟล์เดิม";
const ESTIMATE = "รอบของ agent นี้ใช้ราว 1,134.58 เครดิต (≈ 39.71 บาท) คิดเครดิตเมื่องานเสร็จและส่งไฟล์ให้แล้ว ไม่ว่ายอดจะลงตัวหรือไม่ รอบที่ระบบขัดข้องไม่คิดเครดิต";

const completion: Completion = {
  code: "not_tied",
  title: OPEN_TITLE,
  what: "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท",
  next: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร",
  files: "ไฟล์ของรอบนี้ส่งให้แล้วเป็นฉบับร่าง มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ",
  charged: "รอบนี้ใช้ไป 1,134.58 เครดิต งานของรอบนี้เสร็จและส่งไฟล์ให้แล้ว จึงคิดเครดิตตามปกติ แม้ยอดจะยังไม่ลงตัว",
  credits: 1134.58,
};
const completionText = `${completion.title} — ${completion.what} ${completion.next} ${completion.files} ${completion.charged}`;

const crashed: Failure = {
  reason: "render_failed",
  what: "สร้างไฟล์ผลงานไม่สำเร็จ ตัวเลขคำนวณเสร็จแล้วแต่ยังไม่ได้เป็นไฟล์",
  next: "กด “ลองใหม่ด้วยไฟล์เดิม” ได้เลย",
  files: "ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบ",
  charged: "รอบนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้",
  credits: 0,
  can_retry: true,
};

const at = (s: number) => new Date(Date.UTC(2026, 8, 29, 3, 0, s)).toISOString();
function item(id: string, kind: string, payload: unknown, s: number, run: string | null = RUN): ThreadItem {
  return { id, install_id: "i-1", kind, rev: s + 1, run_id: run ?? undefined, created_at: at(s), payload } as ThreadItem;
}

const draft: OutputsPayload = {
  run_id: RUN,
  period_label: "กันยายน 2569",
  draft: true,
  gate: { passed: false, what: completion.what, next: completion.next },
  artifacts: [{ id: "a1", name: "bank-reconciliation_2569-09_tables_v1.xlsx", format: "xlsx", size: 20480, version: 1 }],
  summary: { period_label: "กันยายน 2569", kind: "bank_reconciliation", tied: false, difference: "-21435.00", adjusted_bank: "1517119.65", adjusted_book: "1538554.65" },
  model: { uses: "none" },
};
const openProgress: ProgressPayload = {
  run_id: RUN,
  kind: "run",
  status: "failed",
  outcome: "completed_open",
  reason: "not_tied",
  steps: [
    { key: "render", label: "สร้างไฟล์ผลลัพธ์", status: "done" },
    { key: "gate", label: "ตรวจว่างานของรอบนี้ครบตามเกณฑ์ของ agent", status: "open", detail: completion.what },
  ],
};
const openRound = (run = RUN, from = 0): ThreadItem[] => [
  item(`p-${run}`, "progress", { ...openProgress, run_id: run }, from + 1, run),
  item(`o-${run}`, "outputs", { ...draft, run_id: run }, from + 2, run),
  item(`c-${run}`, "agent_text", { run_id: run, tone: "open", text: completionText, completion }, from + 3, run),
];

const ws: WorkspaceCtx = { installId: "i-1", merge: () => {}, threadId: null };
const inWorkspace = (node: React.ReactNode) => render(<WorkspaceContext.Provider value={ws}>{node}</WorkspaceContext.Provider>);
const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(async () => {
  resetAgentMarketForTest();
  raw.mockReset();
  upload.mockReset();
  upload.mockImplementation(async (_installId: string, f: File) => ({ id: `f-${f.name}`, name: f.name, size: f.size }));
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true }, admin: false, publisher: false });
    if (path.includes("/events")) return new Promise(() => {});
    return handler(path, init);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  mockApi.getKnowledgeAvailability.mockReset();
  mockApi.getKnowledgeAvailability.mockResolvedValue({ available: false });
  mockApi.listKnowledgeBases.mockReset();
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [] });
  useI18nStore.setState({ locale: "en" });
  window.localStorage.clear();
  await useKnowledgeStore.getState().loadAvailability(true);
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AM6 — a round that finished with its gate open is a result", () => {
  const message = item("c", "agent_text", { run_id: RUN, tone: "open", text: completionText, completion }, 3);

  it("is a calm card headed by the server's title, then what, next, files, charged", () => {
    inWorkspace(<ThreadItems items={[message]} />);
    const card = screen.getByTestId("am-round-open");
    expect(within(card).getByRole("heading").textContent).toBe(OPEN_TITLE);
    expect(within(card).getByTestId("am-open-what").textContent).toBe(completion.what);
    expect(within(card).getByTestId("am-open-next").textContent).toBe(completion.next);
    expect(within(card).getByTestId("am-open-files").textContent).toBe(completion.files);
    expect(within(card).getByTestId("am-open-charged").textContent).toBe(completion.charged);
    // said once: the parts are not repeated as one paragraph
    expect(card.textContent!.split(completion.charged).length).toBe(2);
    expect(card.textContent!.split(OPEN_TITLE).length).toBe(2);
    expect(card.textContent).not.toContain("not_tied");
  });

  it("is not the failed-turn card, not an alert, and in no error colour", () => {
    inWorkspace(<ThreadItems items={[message]} />);
    expect(screen.getByTestId("am-round-open")).toBeTruthy();
    expect(screen.queryByTestId("am-failed-round")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("am-agent-group").innerHTML).not.toContain("status-danger");
  });

  it("does not offer the same files again", () => {
    inWorkspace(<ThreadItems items={[message]} />);
    expect(screen.getByTestId("am-round-open")).toBeTruthy();
    expect(screen.queryByTestId("am-failed-retry")).toBeNull();
    expect(screen.queryByText(RETRY)).toBeNull();
    expect(within(screen.getByTestId("am-round-open")).getByTestId("am-open-run").getAttribute("href")).toBe(`/chat/agents/runs/${RUN}`);
  });

  it("a gate of another agent is headed by its own title", () => {
    const other = { ...completion, code: "po_gate", title: OPEN_OTHER };
    inWorkspace(<ThreadItems items={[item("c", "agent_text", { run_id: RUN, tone: "open", text: "x", completion: other }, 3)]} />);
    expect(within(screen.getByTestId("am-round-open")).getByRole("heading").textContent).toBe(OPEN_OTHER);
  });

  it("a round that crashed keeps the failed-turn card, and says nothing was charged", () => {
    inWorkspace(<ThreadItems items={[item("f", "agent_text", { run_id: RUN, tone: "error", text: crashed.what, failure: crashed }, 3)]} />);
    const card = screen.getByTestId("am-failed-round");
    expect(within(card).getByRole("alert")).toBeTruthy();
    expect(within(card).getByTestId("am-failed-charged").textContent).toBe("รอบนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้");
    expect(within(card).getByTestId("am-failed-retry").textContent).toContain(RETRY);
    expect(screen.queryByTestId("am-round-open")).toBeNull();
  });
});

describe("AM6 — the progress card goes by the outcome", () => {
  it("not tied: a finished round with a note, never a failure", () => {
    render(<ProgressCard progress={openProgress} />);
    const card = screen.getByTestId("am-progress");
    expect(screen.getByTestId("am-progress-title").textContent).toBe(OPEN_TITLE);
    expect(card.getAttribute("data-outcome")).toBe("completed_open");
    expect(card.className).not.toContain("status-danger");
    expect(card.innerHTML).not.toContain("status-danger");
    expect(card.textContent).not.toContain("ไม่สำเร็จ");
    // nothing is left to cancel
    expect(screen.queryByTestId("am-progress-cancel")).toBeNull();
  });

  it("a step that is open is a finished step with a note", () => {
    render(<ProgressCard progress={openProgress} />);
    const gate = screen.getByTestId("am-progress").querySelector('[data-step-status="open"]') as HTMLElement;
    expect(gate.textContent).toContain(completion.what);
    expect(gate.textContent).toContain("เสร็จแล้ว มีหมายเหตุ");
    expect(gate.querySelector('[data-step-icon="open"]')).toBeTruthy();
    expect(gate.innerHTML).not.toContain("status-danger");
  });

  it("the gate of another agent", () => {
    render(<ProgressCard progress={{ ...openProgress, reason: "lines_unmatched" }} />);
    expect(screen.getByTestId("am-progress-title").textContent).toBe(OPEN_OTHER);
  });

  it("a server that sends no outcome is read by its status", () => {
    render(<ProgressCard progress={{ run_id: RUN, kind: "run", status: "failed", reason: "render_failed", steps: [{ key: "render", label: "สร้างไฟล์ผลลัพธ์", status: "failed" }] }} />);
    expect(screen.getByTestId("am-progress-title").textContent).toBe("ไม่สำเร็จ");
    expect(screen.getByTestId("am-progress").className).toContain("border-status-danger");
  });

  it("a round that failed is still a failure", () => {
    render(<ProgressCard progress={{ run_id: RUN, kind: "run", status: "failed", outcome: "failed", reason: "render_failed" }} />);
    expect(screen.getByTestId("am-progress-title").textContent).toBe("ไม่สำเร็จ");
    expect(screen.getByTestId("am-progress").className).toContain("border-status-danger");
  });
});

describe("AM6 — the round's divider and its files card", () => {
  it("the divider names the round as finished, with its note", () => {
    inWorkspace(<ThreadView items={openRound()} />);
    const divider = screen.getByTestId("am-round-divider");
    expect(within(divider).getByRole("separator").getAttribute("aria-label")).toBe(`รอบ กันยายน 2569 · ${OPEN_TITLE}`);
    expect(divider.getAttribute("data-outcome")).toBe("completed_open");
    expect(divider.textContent).not.toContain("ไม่สำเร็จ");
    expect(divider.innerHTML).not.toContain("status-danger");
  });

  it("an older round folds under the same words", () => {
    const newer = "11111111-2222-4333-8444-555555555555";
    inWorkspace(<ThreadView items={[...openRound(), ...openRound(newer, 10)]} />);
    const folded = screen.getByTestId("am-round-folded");
    expect(folded.textContent).toContain(`รอบ กันยายน 2569 · ${OPEN_TITLE}`);
    expect(folded.textContent).not.toContain("ไม่สำเร็จ");
  });

  it("a round of a server that sends no outcome reads as it did", () => {
    const old = openRound().map((it) => (it.kind === "progress" ? { ...it, payload: { run_id: RUN, kind: "run", status: "failed", reason: "not_tied" } } : it));
    inWorkspace(<ThreadView items={old} />);
    expect(within(screen.getByTestId("am-round-divider")).getByRole("separator").getAttribute("aria-label")).toBe("รอบ กันยายน 2569 · ไม่สำเร็จ");
  });

  it("the files card of such a round: a draft that is done, and why it cannot be signed", () => {
    inWorkspace(<ThreadView items={openRound()} />);
    const card = screen.getByTestId("am-outputs");
    const banner = within(card).getByTestId("am-outputs-draft");
    expect(banner.textContent).toContain("ร่าง");
    expect(banner.textContent).toContain(completion.what);
    expect(banner.textContent).not.toContain("ยังไม่นับว่าสำเร็จ");
    expect(banner.getAttribute("role")).toBe("status");
    expect(within(card).queryByTestId("am-signoff-open")).toBeNull();
    const why = within(card).getByTestId("am-signoff-unavailable");
    expect(why.textContent).toContain("ยังตรวจทานและลงชื่อรอบนี้ไม่ได้");
    expect(why.textContent).toContain(completion.what);
  });

  it("a files card outside a round says what it said", () => {
    inWorkspace(<OutputsCard outputs={draft} />);
    expect(screen.getByTestId("am-outputs-draft").getAttribute("role")).toBe("alert");
  });
});

describe("AM6 — what a round costs is said before it starts", () => {
  const manifest = {
    id: "sran.bank-reconciliation",
    name: "กระทบยอดเงินฝากธนาคาร",
    short_name: "กระทบยอดธนาคาร",
    greeting: { text: "สวัสดีครับ", role: "ผู้ช่วยกระทบยอดธนาคาร", tagline: "อ่านอย่างเดียว" },
    inputs: [{ role: "statement", label: "รายการเดินบัญชีธนาคาร", required: true, accept: [".xlsx"] }],
    outputs: [{ format: "xlsx", label: "กระดาษทำการ", default: true }],
  } as unknown as Manifest;
  const greeting = item("g", "greeting", { text: "สวัสดีครับ" }, 0, null);
  const finished: ThreadItem[] = [
    greeting,
    item("p", "progress", { run_id: RUN, kind: "run", status: "succeeded", outcome: "completed" }, 1),
    item("o", "outputs", { run_id: RUN, period_label: "กันยายน 2569", artifacts: draft.artifacts, model: { uses: "none" } }, 2),
  ];
  const underway: ThreadItem[] = [greeting, item("p", "progress", { run_id: RUN, kind: "run", status: "running", outcome: "running" }, 1)];
  const estimate = { basis: "own_runs", basis_runs: 3, credits: 1134.58, thb: 39.71, seconds: 40, line: ESTIMATE };

  const workspace = (items: ThreadItem[], est: () => unknown): Handler => (path) => {
    if (path === "/agent-market/installs/i-1/estimate") return est();
    if (path === "/agent-market/installs") return json({ installs: [{ id: "i-1", slug: "sran.bank-reconciliation", title: "กระทบยอดธนาคาร", created_at: "" }] });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.bank-reconciliation", title: "กระทบยอดธนาคาร" }, manifest, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items, cursor: 99 });
    if (path === "/agent-market/installs/i-1/models")
      return json({ uses: "none", choice: "auto", auto_model: "", models: [], blocked_reason: "", hidden_reason: "", reads: "", note: "" });
    if (path === "/agent-market/installs/i-1/storage")
      return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    if (path.startsWith("/agent-market/runs/")) return json({ exceptions: [] });
    return json({ ok: true });
  };
  const asked = () => raw.mock.calls.filter((c) => c[0] === "/agent-market/installs/i-1/estimate").length;
  const attach = (...names: string[]) =>
    fireEvent.change(screen.getByTestId("am-composer-file"), { target: { files: names.map((n) => new File(["x"], n)) } });

  it("on the empty workspace: one quiet line, the server's sentence", async () => {
    handler = workspace([greeting], () => json(estimate));
    render(<WorkspacePage installId="i-1" />);
    const empty = await screen.findByTestId("am-empty-drop");
    const line = await within(empty).findByTestId("am-estimate");
    expect(line.textContent).toBe(ESTIMATE);
    expect(line.tagName).toBe("P");
    expect(line.className).not.toMatch(/\bborder\b|\bbg-/);
    expect(screen.getAllByTestId("am-estimate")).toHaveLength(1);
  });

  it("nothing is shown while the request is loading", async () => {
    handler = workspace([greeting], () => new Promise(() => {}));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-empty-drop");
    await waitFor(() => expect(asked()).toBe(1));
    await flush();
    expect(screen.queryByTestId("am-estimate")).toBeNull();
  });

  it("nothing is shown when the request has failed", async () => {
    handler = workspace([greeting], () => json({ error: { code: "INTERNAL", message: "เกิดข้อผิดพลาด" } }, 500));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-empty-drop");
    await waitFor(() => expect(asked()).toBe(1));
    await flush();
    expect(screen.queryByTestId("am-estimate")).toBeNull();
    expect(screen.queryByText("เกิดข้อผิดพลาด")).toBeNull();
  });

  it("nothing is shown when the server has no sentence", async () => {
    handler = workspace([greeting], () => json({ basis: "none", basis_runs: 0, credits: null, thb: null, seconds: null, line: "" }));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-empty-drop");
    await waitFor(() => expect(asked()).toBe(1));
    await flush();
    expect(screen.queryByTestId("am-estimate")).toBeNull();
  });

  it("above the composer while files are staged, not before", async () => {
    handler = workspace(finished, () => json(estimate));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-round-divider");
    await waitFor(() => expect(asked()).toBe(1));
    await flush();
    expect(screen.queryByTestId("am-estimate")).toBeNull();
    attach("statement_2569-10.xlsx");
    const line = await within(screen.getByTestId("composer-root")).findByTestId("am-estimate");
    expect(line.textContent).toBe(ESTIMATE);
    expect(line.className).not.toMatch(/\bborder\b|\bbg-/);
  });

  it("not while a round is underway", async () => {
    handler = workspace(underway, () => json(estimate));
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-progress");
    attach("statement_2569-10.xlsx");
    await screen.findByTestId("am-staged-chip");
    await flush();
    expect(screen.queryByTestId("am-estimate")).toBeNull();
  });
});
