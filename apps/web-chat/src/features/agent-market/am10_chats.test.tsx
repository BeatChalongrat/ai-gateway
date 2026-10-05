// AM10 — chats under each agent.
//   * the sidebar opens an installed agent to its chats: "แชทใหม่กับ agent นี้" on
//     top, then the chats, pinned first, each with rename / pin / open beside / delete;
//   * deleting asks first, and says the run pages, files and audit rows stay;
//     the chat on screen moves to the agent's workspace afterwards;
//   * a chat that was deleted elsewhere (THREAD_GONE) sends the workspace to the
//     agent's latest chat — a notification's round to its run page;
//   * ?beside=<chat> reads a second chat beside the first, each with its own thread.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, push, replace } = vi.hoisted(() => ({ raw: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  usePathname: () => "/chat/agents/i/i-1",
  useSearchParams: () => new URLSearchParams("thread=t-2"),
}));

import { useI18nStore } from "@/lib/i18n";

import { AgentRailSection } from "./AgentRailSection";
import { besideHref, currentChatKey } from "./ChatRows";
import type { AgentThread, Manifest, RailInstall, ThreadItem } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { WorkspacePage } from "./workspace/WorkspacePage";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const calls = (path: string, method?: string) => raw.mock.calls.filter((c) => c[0] === path && (!method || (c[1] as RequestInit | undefined)?.method === method));

const install: RailInstall = { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน", icon: "chart", version: "1.0.0", pinned_at: null, last_used_at: null, waiting: 0, disabled: false };
let chats: AgentThread[];
const chat = (id: string | null, title: string, extra: Partial<AgentThread> = {}): AgentThread => ({
  id, title, created_at: "2026-09-28T03:00:00Z", last_at: "2026-09-28T03:00:00Z", rounds: 1, waiting: false, pinned_at: null, named: false, ...extra,
});

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  push.mockReset();
  replace.mockReset();
  useI18nStore.setState({ locale: "th" });
  chats = [chat("t-2", "กันยายน 2569", { pinned_at: "2026-09-29T00:00:00Z" }), chat(null, "แชทแรก", { rounds: 2 }), chat("t-3", "ตุลาคม 2569", { waiting: true })];
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
    if (path === "/agent-market/rail") return json({ installs: [install], waiting_total: 0, prefs: {} });
    if (path === "/agent-market/installs/i-1/threads" && !init?.method) return json({ threads: chats });
    if (path.includes("/events")) return new Promise(() => {});
    return handler(path, init);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  try {
    window.localStorage.clear();
  } catch {
    /* none */
  }
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the sidebar lists each agent's chats", () => {
  it("the agent on screen opens to its chats, pinned first, with a new-chat row on top", async () => {
    render(<AgentRailSection />);
    const rows = await screen.findAllByTestId("agent-chat-row");
    expect(rows.map((r) => r.getAttribute("data-chat"))).toEqual(["t-2", "main", "t-3"]);
    expect(screen.getByTestId("agent-chat-new").textContent).toContain("แชทใหม่กับ agent นี้");
    // the chat on screen (?thread=t-2) is marked; names and counts as the server says them
    expect(rows[0]!.getAttribute("data-current")).toBe("true");
    expect(within(rows[0]!).getByTestId("agent-chat-link").getAttribute("href")).toBe("/chat/agents/i/i-1?thread=t-2");
    expect(within(rows[1]!).getByTestId("agent-chat-link").getAttribute("href")).toBe("/chat/agents/i/i-1");
    expect(within(rows[1]!).getByTestId("agent-chat-link").textContent).toContain("2 รอบ");
    expect(within(rows[2]!).getByTestId("agent-chat-link").textContent).toContain("รอคำตอบ");
    // closing it hides them; the choice survives in this browser
    fireEvent.click(screen.getByTestId("agent-rail-chats-toggle"));
    expect(screen.queryByTestId("agent-chat-rows")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("am.rail.opened") ?? "[]")).toEqual(["i-1"]);
  });

  it("a new chat is made on the server and opened", async () => {
    handler = (path, init) => {
      if (path === "/agent-market/installs/i-1/threads" && init?.method === "POST") return json({ thread: chat("t-9", "แชทใหม่ · 5 ต.ค. 2569", { rounds: 0 }) });
      return json({}, 404);
    };
    render(<AgentRailSection />);
    fireEvent.click(await screen.findByTestId("agent-chat-new"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-9"));
  });

  it("rename and pin go to the server for that chat — the first chat is 'main'", async () => {
    handler = (path, init) => {
      if (path === "/agent-market/installs/i-1/threads/main" && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as { title?: string; pinned?: boolean };
        const next = chat(null, body.title ?? "แชทแรก", { named: !!body.title, pinned_at: body.pinned ? "2026-10-05T00:00:00Z" : null });
        chats = chats.map((c) => (c.id === null ? next : c));
        return json({ thread: next });
      }
      return json({}, 404);
    };
    render(<AgentRailSection />);
    const rows = await screen.findAllByTestId("agent-chat-row");
    const first = rows[1]!;
    fireEvent.click(within(first).getByTestId("agent-chat-menu"));
    fireEvent.click(within(first).getByTestId("agent-chat-rename"));
    const input = within(first).getByTestId("agent-chat-rename-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "งบ Q3 ส่งบอร์ด" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(calls("/agent-market/installs/i-1/threads/main", "PATCH").length).toBe(1));
    expect(JSON.parse(String((calls("/agent-market/installs/i-1/threads/main", "PATCH")[0]![1] as RequestInit).body))).toEqual({ title: "งบ Q3 ส่งบอร์ด" });
    await screen.findByText("งบ Q3 ส่งบอร์ด");
    fireEvent.click(within(screen.getAllByTestId("agent-chat-row")[1]!).getByTestId("agent-chat-menu"));
    fireEvent.click(screen.getByTestId("agent-chat-pin"));
    await waitFor(() => expect(calls("/agent-market/installs/i-1/threads/main", "PATCH").length).toBe(2));
    expect(JSON.parse(String((calls("/agent-market/installs/i-1/threads/main", "PATCH")[1]![1] as RequestInit).body))).toEqual({ pinned: true });
  });

  it("delete asks first and says the evidence stays; the chat on screen then moves to the workspace", async () => {
    handler = (path, init) => {
      if (path === "/agent-market/installs/i-1/threads/t-2" && init?.method === "DELETE") {
        chats = chats.filter((c) => c.id !== "t-2");
        return json({ deleted: true, cancelled_runs: 0, kept_runs: 1 });
      }
      return json({}, 404);
    };
    render(<AgentRailSection />);
    const rows = await screen.findAllByTestId("agent-chat-row");
    fireEvent.click(within(rows[0]!).getByTestId("agent-chat-menu"));
    fireEvent.click(within(rows[0]!).getByTestId("agent-chat-delete"));
    const dialog = await screen.findByTestId("agent-chat-delete-dialog");
    expect(calls("/agent-market/installs/i-1/threads/t-2", "DELETE").length).toBe(0);
    const body = within(dialog).getByTestId("agent-chat-delete-body").textContent ?? "";
    for (const word of ["หน้ารอบงาน", "ไฟล์ที่ส่งมอบ", "บันทึกการตรวจ", "ยังอยู่ครบ"]) expect(body).toContain(word);
    expect(within(dialog).getByText("รอบงานทั้งหมดของ agent นี้เปิดได้จากหน้าภาพรวม").getAttribute("href")).toBe("/chat/agents/i/i-1/dashboard");
    fireEvent.click(within(dialog).getByTestId("agent-chat-delete-confirm"));
    await waitFor(() => expect(calls("/agent-market/installs/i-1/threads/t-2", "DELETE").length).toBe(1));
    await waitFor(() => expect(screen.queryByTestId("agent-chat-delete-dialog")).toBeNull());
    expect(screen.getAllByTestId("agent-chat-row").map((r) => r.getAttribute("data-chat"))).toEqual(["main", "t-3"]);
    expect(replace).toHaveBeenCalledWith("/chat/agents/i/i-1");
  });

  it("a chat with a round still waiting says it will be cancelled", async () => {
    render(<AgentRailSection />);
    const rows = await screen.findAllByTestId("agent-chat-row");
    fireEvent.click(within(rows[2]!).getByTestId("agent-chat-menu"));
    fireEvent.click(within(rows[2]!).getByTestId("agent-chat-delete"));
    expect((await screen.findByTestId("agent-chat-delete-dialog")).textContent).toContain("จะถูกยกเลิก");
  });

  it("open beside: the chat on screen stays, the other joins it", async () => {
    render(<AgentRailSection />);
    const rows = await screen.findAllByTestId("agent-chat-row");
    fireEvent.click(within(rows[2]!).getByTestId("agent-chat-menu"));
    fireEvent.click(within(rows[2]!).getByTestId("agent-chat-beside"));
    expect(push).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-2&beside=t-3");
    // the chat on screen offers no "beside" of itself
    fireEvent.click(within(rows[0]!).getByTestId("agent-chat-menu"));
    expect(screen.queryByTestId("agent-chat-beside")).toBeNull();
    expect(besideHref("i-1", "main", "t-3")).toBe("/chat/agents/i/i-1?beside=t-3");
    expect(currentChatKey("thread=main")).toBe("main");
    expect(currentChatKey(null)).toBe("main");
  });
});

const manifest = { id: "sran.financial-report", name: "รายงานการเงิน", greeting: { text: "สวัสดีครับ", role: "ผู้ช่วยปิดงบการเงิน" }, inputs: [], outputs: [] } as Manifest;
const item = (id: string, text: string): ThreadItem => ({ id, install_id: "i-1", kind: "greeting", payload: { text }, rev: 1, created_at: "2026-09-28T03:00:00Z" });

function workspaceHandler(threads: Record<string, ThreadItem[] | "gone">): Handler {
  return (path, init) => {
    if (path === "/agent-market/installs") return json({ installs: [{ id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน" }] });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน" }, manifest, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread?") || path === "/agent-market/installs/i-1/thread") {
      const key = new URLSearchParams(path.split("?")[1] ?? "").get("thread") ?? "main";
      const items = threads[key];
      if (items === "gone") return json({ error: { code: "THREAD_GONE", message: "แชทนี้ถูกลบออกจากรายการแล้ว" } }, 404);
      return json({ items: items ?? [], cursor: 9 });
    }
    if (path === "/agent-market/installs/i-1/threads" && init?.method === "POST") return json({ thread: chat("t-new", "แชทใหม่", { rounds: 0 }) });
    if (path === "/agent-market/installs/i-1/models") return json({ uses: "text", choice: "auto", auto_model: "", models: [], blocked_reason: "", hidden_reason: "", reads: "", note: "" });
    if (path === "/agent-market/installs/i-1/storage") return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    if (path === "/agent-market/installs/i-1/opened") return json({ ok: true });
    if (path === "/agent-market/installs/i-1/estimate") return json({ basis: "none", basis_runs: 0, credits: null, thb: null, seconds: null, line: "" });
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
}

describe("the workspace with a chat that is gone, and with one beside it", () => {
  it("a deleted chat sends the person to the agent's latest chat, and says so", async () => {
    handler = workspaceHandler({ "t-2": "gone" });
    render(<WorkspacePage installId="i-1" threadId="t-2" />);
    await screen.findByTestId("am-ws-chat-gone");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-2"));
  });

  it("a notification's round in a deleted chat opens on its run page", async () => {
    handler = workspaceHandler({ main: "gone" });
    render(<WorkspacePage installId="i-1" runId="r-7" />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat/agents/runs/r-7"));
  });

  it("with no chat left, a new one is started", async () => {
    chats = [];
    handler = workspaceHandler({ main: "gone" });
    render(<WorkspacePage installId="i-1" />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-new"));
  });

  it("?beside= reads the second chat beside the first, each from its own thread", async () => {
    handler = workspaceHandler({ main: [item("a", "สวัสดี แชทแรก")], "t-3": [item("b", "สวัสดี แชทตุลาคม")] });
    render(<WorkspacePage installId="i-1" beside="t-3" />);
    await screen.findByText("สวัสดี แชทแรก");
    const pane = await screen.findByTestId("am-beside");
    expect(pane.getAttribute("data-chat")).toBe("t-3");
    await within(pane).findByText("สวัสดี แชทตุลาคม");
    await waitFor(() => expect(screen.getByTestId("am-beside-title").textContent).toBe("ตุลาคม 2569"));
    expect(screen.getByTestId("am-workspace").getAttribute("data-beside")).toBe("t-3");
    // one composer, in the chat on screen
    expect(screen.getAllByTestId("am-thread").length).toBe(1);
    expect(screen.getByTestId("am-thread-beside")).toBeTruthy();
    fireEvent.click(screen.getByTestId("am-beside-swap"));
    expect(push).toHaveBeenCalledWith("/chat/agents/i/i-1?thread=t-3&beside=main");
    fireEvent.click(screen.getByTestId("am-beside-close"));
    expect(push).toHaveBeenCalledWith("/chat/agents/i/i-1");
  });
});
