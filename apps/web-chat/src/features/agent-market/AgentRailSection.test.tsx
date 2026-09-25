// AM1 — the sidebar entry is absent unless the feature is on.
//   * flag off (/api/agent-market says enabled:false) → renders nothing and
//     makes NO chat-edge request at all;
//   * no fetch / a failing fetch → renders nothing, throws nothing;
//   * flag on + account allowed → the market link and my installed agents,
//     with the attention dot on one waiting for an answer.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

import { useI18nStore } from "@/lib/i18n";

import { AgentRailSection } from "./AgentRailSection";
import { resetAgentMarketForTest } from "./useAgentMarket";

const json = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) });

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentRailSection", () => {
  it("flag off: renders nothing and never calls chat-edge", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: false }) });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<AgentRailSection />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/agent-market");
    // give the (absent) second step a chance to run
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("no fetch at all (a test or an old shell): renders nothing and does not throw", async () => {
    vi.stubGlobal("fetch", undefined);
    const { container } = render(<AgentRailSection />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("a failing flag request: renders nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to parse URL")));
    const { container } = render(<AgentRailSection />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("flag on but the account is not allowed: renders nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) }));
    raw.mockResolvedValue(json({ available: false, runner: { healthy: true } }));
    const { container } = render(<AgentRailSection />);
    await waitFor(() => expect(raw).toHaveBeenCalledWith("/agent-market/availability", expect.anything()));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
  });

  it("available: the market link and the installed agents, attention marked", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) }));
    raw.mockImplementation(async (path: string) => {
      if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
      if (path === "/agent-market/installs")
        return json({
          installs: [
            { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน", definition_version: "1.0.0", created_at: "2026-09-26T00:00:00Z", icon: "chart", attention: true },
          ],
        });
      return json({ error: { code: "NOT_FOUND", message: "x" } }, 404);
    });
    render(<AgentRailSection />);
    const section = await screen.findByTestId("agent-rail-section");
    expect(section.querySelector('a[href="/chat/agents"]')?.textContent).toContain("Agent Market");
    const item = await screen.findByTestId("agent-rail-install");
    expect(item.getAttribute("href")).toBe("/chat/agents/i/i-1");
    expect(item.getAttribute("aria-current")).toBe("page");
    expect(item.textContent).toContain("ปิดงบการเงิน");
    expect(item.textContent).toContain("รอคำตอบของคุณ");
  });
});
