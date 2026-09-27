// AM2 — the new screens against what the server really sends. The payloads in
// __fixtures__/am2_real_api.json were captured from the test stack by the AM2
// probes (an expense run's exceptions, an invoice agent's dashboard, the
// upgrade of an install pinned to 1.0.0); the other tests use hand-written
// shapes, this one catches a field the server names differently.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import real from "./__fixtures__/am2_real_api.json";
import { DashboardPage } from "./dashboard/DashboardPage";
import { RunExceptions } from "./exceptions/ExceptionsPanel";
import { UpgradeSection } from "./upgrade/UpgradeSection";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

beforeEach(() => {
  raw.mockReset();
  raw.mockImplementation(async (path: string) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
    if (path === "/agent-market/installs/i-1") {
      return json({ install: { id: "i-1", slug: "sran.invoice-processing", title: "ทะเบียนซื้อ", definition_version: "1.0.0", created_at: "" },
        manifest: {}, defaults: [], budget: { credits: 3000000, spent_this_month: 41316 } });
    }
    if (path.endsWith("/exceptions")) return json(real.exceptions);
    if (path.includes("/dashboard")) return json(real.dashboard);
    if (path.endsWith("/upgrade")) return json(real.upgrade);
    return json({}, 404);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AM2 screens with the server's own payloads", () => {
  it("exceptions: a decided row, open rows with their choices, the table's limit and the KB as context", async () => {
    render(<RunExceptions runId={real.exceptions.exceptions[0]!.run_id} />);
    const panel = await screen.findByTestId("am-exceptions");
    expect(screen.getByTestId("am-exceptions-counts").textContent).toBe("รอตัดสิน 2 · ตัดสินแล้ว 1");
    fireEvent.click(screen.getByText("ตัดสินแล้ว 1 ข้อ"));
    expect(panel.textContent).toContain("ตัดสินแล้ว: อนุมัติเป็นกรณียกเว้น");
    expect(panel.textContent).toContain("[AM2] ทดสอบ"); // the note given with the decision
    const open = screen.getAllByTestId("am-exception");
    expect(open.length).toBe(2);
    for (const row of open) expect(within(row).getAllByTestId("am-exception-choice").length).toBeGreaterThan(0);
    expect(panel.textContent).toContain("จากตารางนโยบาย");
    expect(panel.textContent).toContain("ใช้เป็นบริบทเท่านั้น ไม่ใช่วงเงิน");
    expect(panel.textContent).not.toMatch(/ปฏิเสธ|reject/i);
  });

  it("dashboard: the invoice agent's real figures, the self-hosted model marked in the organisation", async () => {
    render(<DashboardPage installId="i-1" />);
    const models = await screen.findByTestId("am-dash-models");
    expect(models.textContent).toContain("qwen3.8-27b-fp8");
    expect(models.textContent).toContain("ในองค์กร");
    expect(screen.getAllByTestId("am-dash-tile").length).toBe(5);
    const keys = screen.getAllByTestId("am-dash-bar").map((b) => b.getAttribute("data-key"));
    expect(keys).toEqual(expect.arrayContaining(["e2_low_confidence", "e2_duplicate", "scan", "digital", "high"]));
  });

  it("upgrade: the version pinned before AM2, the newer one and what changes", async () => {
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} />);
    const avail = await screen.findByTestId("am-upgrade-available");
    expect(screen.getByTestId("am-upgrade-current").textContent).toContain("1.0.0");
    expect(avail.textContent).toContain("1.1.0");
    expect(avail.textContent).toContain("outputs");
    expect(within(avail).getByTestId("am-upgrade-start")).toBeTruthy();
  });
});
