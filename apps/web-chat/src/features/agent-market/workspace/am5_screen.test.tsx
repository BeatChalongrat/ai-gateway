// AM5 — what the screen tour on the test stack found that no unit test had:
//   * the workspace had a navigation column of its own ("Agent ของฉัน", 200 px)
//     beside the app's sidebar, which lists the same agents: the agent's
//     column sat 100 px to the right of where the chat's sits;
//   * the model menu opened to the right of its pill whatever room there
//     was, so in the workspace it ran off the window's edge and the page
//     scrolled sideways.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/agents/i/i-1",
}));

import { ModelPopover } from "@/components/chat/ModelSelector";

import type { Manifest } from "../types";
import { resetAgentMarketForTest } from "../useAgentMarket";
import { WorkspacePage } from "./WorkspacePage";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

const manifest = {
  id: "sran.financial-report",
  name: "รายงานการเงิน",
  icon: "report",
  greeting: { text: "สวัสดีครับ", role: "ผู้ช่วยปิดงบการเงิน", tagline: "อ่านอย่างเดียว" },
  inputs: [{ role: "primary", label: "บัญชีแยกประเภท", required: true, accept: [".xlsx"] }],
  outputs: [{ format: "docx", label: "รายงาน", default: true }],
} as unknown as Manifest;

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  raw.mockImplementation(async (path: string) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true }, admin: false, publisher: false });
    if (path.includes("/events")) return new Promise(() => {});
    if (path === "/agent-market/installs")
      return json({
        installs: [
          { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน", created_at: "" },
          { id: "i-2", slug: "sran.invoice-processing", title: "ทะเบียนเอกสารซื้อ", created_at: "" },
        ],
      });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน" }, manifest, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread"))
      return json({ items: [{ id: "g", install_id: "i-1", kind: "greeting", payload: { text: "สวัสดีครับ" }, rev: 1, created_at: new Date().toISOString() }], cursor: 9 });
    if (path === "/agent-market/installs/i-1/models") return json({ uses: "text", reads: "", choice: "auto", documents_may_leave: false, note: "", options: [] });
    if (path === "/agent-market/installs/i-1/storage")
      return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    return json({ ok: true });
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the workspace sits where the chat sits", () => {
  it("has no list of agents of its own: the app's sidebar is the one list", async () => {
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-empty-sentence");
    const ws = screen.getByTestId("am-workspace");
    expect(screen.queryByTestId("am-rail")).toBeNull();
    expect(ws.querySelector("nav")).toBeNull();
    // the other agent is not named anywhere inside the workspace
    expect(ws.textContent ?? "").not.toContain("ทะเบียนเอกสารซื้อ");
    // nothing beside the column: the thread and the composer are the page
    expect(ws.children).toHaveLength(1);
  });

  it("says which agent this is in its header at every width", async () => {
    render(<WorkspacePage installId="i-1" />);
    await screen.findByTestId("am-empty-sentence");
    const tile = screen.getByTestId("am-ws-tile");
    expect(tile.className).not.toMatch(/\bhidden\b|md:hidden/);
  });
});

describe("the model menu opens where there is room", () => {
  const open = (right: number, width: number) => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const left = right - 320;
      return { left, right, top: 300, bottom: 700, width: 320, height: 400, x: left, y: 300, toJSON: () => ({}) } as DOMRect;
    });
    render(
      <ModelPopover label="โมเดล" onClose={() => {}} testId="menu">
        <div>row</div>
      </ModelPopover>,
    );
    return screen.getByTestId("menu");
  };

  it("to the right of its pill when it fits (the chat, a wide window)", () => {
    const menu = open(1316, 1360);
    expect(menu.getAttribute("data-align")).toBe("start");
    expect(menu.className).toContain("sm:left-0");
    expect(menu.className).not.toContain("sm:right-0");
  });

  it("to the left of its pill when it would leave the window", () => {
    const menu = open(1436, 1360);
    expect(menu.getAttribute("data-align")).toBe("end");
    expect(menu.className).toContain("sm:right-0");
    expect(menu.className).not.toContain("sm:left-0");
  });
});
