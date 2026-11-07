// AM2 phase G — upgrading and rolling back in settings.
//   * a newer version shows its changes grouped (instructions as -/+ lines);
//   * "test with the sample data, then upgrade" posts the new version, polls
//     its self-test run until it ends, and reads the outcome from the
//     install's version afterwards — the run can say "succeeded" a moment
//     before the install switches, and a failed test keeps the old version;
//   * rolling back posts /rollback; nothing to roll back to (409) says so;
//   * past runs keep their own version — said once.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import type { UpgradeInfo, VersionChange } from "../types";
import { diffLine, groupChanges, upgradeOutcome } from "./upgrade";
import { UpgradeSection } from "./UpgradeSection";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const v = (id: string, version: string) => ({ id, version, name: "รายงานการเงิน", checksum: "c", released_at: "2026-09-20T10:00:00Z" });

const changes: VersionChange[] = [
  { what: "instructions", key: "AGENT.md", before: "อ่านไฟล์ .xlsx" },
  { what: "manifest", key: "summary", before: "\"เดิม\"", after: "\"ใหม่\"" },
  { what: "instructions", key: "AGENT.md", after: "อ่านไฟล์ .xlsx และ SAP" },
  { what: "capability", key: "optional", after: "deliverable.pdf" },
  { what: "setting", key: "formats", before: "[\"docx\",\"pptx\"]", after: "[\"docx\",\"pptx\",\"pdf\"]" },
];
const before: UpgradeInfo = { current: v("d-1", "1.0.0"), available: v("d-2", "1.1.0"), changes };
const after: UpgradeInfo = { current: v("d-2", "1.1.0"), previous: v("d-1", "1.0.0"), available: null };

beforeEach(() => {
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

describe("helpers", () => {
  it("groups changes the way a person checks them, instructions as -/+ lines", () => {
    expect(groupChanges(changes).map((g) => [g.what, g.items.length])).toEqual([
      ["manifest", 1],
      ["capability", 1],
      ["setting", 1],
      ["instructions", 2],
    ]);
    expect(diffLine(changes[0]!)).toEqual({ sign: "-", text: "อ่านไฟล์ .xlsx" });
    expect(diffLine(changes[2]!)).toEqual({ sign: "+", text: "อ่านไฟล์ .xlsx และ SAP" });
    expect(upgradeOutcome("1.1.0", "1.1.0")).toEqual({ kind: "upgraded", version: "1.1.0" });
    expect(upgradeOutcome("1.1.0", "1.0.0")).toEqual({ kind: "kept", version: "1.0.0", tried: "1.1.0" });
  });
});

describe("UpgradeSection", () => {
  it("shows the new version and its changes; says once that past runs keep their version", async () => {
    raw.mockResolvedValue(json(before));
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} />);
    expect((await screen.findByTestId("am-upgrade-current")).textContent).toContain("ใช้รุ่น 1.0.0 อยู่");
    expect(screen.getByText("รอบที่ทำไปแล้วยังเป็นของรุ่นที่ใช้ตอนนั้นเสมอ ไม่ว่าจะอัปเกรดหรือย้อนกลับ")).toBeTruthy();
    const avail = screen.getByTestId("am-upgrade-available");
    expect(avail.textContent).toContain("มีรุ่นใหม่ 1.1.0");
    expect(screen.getAllByTestId("am-upgrade-group").map((g) => g.getAttribute("data-what"))).toEqual(["manifest", "capability", "setting", "instructions"]);
    expect(screen.getByTestId("am-upgrade-lines").textContent).toBe("- อ่านไฟล์ .xlsx+ อ่านไฟล์ .xlsx และ SAP");
    expect(avail.textContent).toContain("+ deliverable.pdf");
    expect(screen.getByTestId("am-upgrade-start").textContent).toBe("ทดสอบกับข้อมูลตัวอย่างแล้วอัปเกรด");
    expect(screen.queryByTestId("am-upgrade-rollback")).toBeNull();
  });

  it("upgrades: polls the self-test, then waits for the install to switch before saying so", async () => {
    let infoCalls = 0;
    let runPolls = 0;
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/installs/i-1/upgrade" && init?.method === "POST") return json({ run: { id: "run-9", status: "queued" } }, 202);
      if (path === "/agent-market/installs/i-1/upgrade") {
        infoCalls++;
        // 1: the first view; 2: the run has just ended but the install has not switched yet; 3: switched
        return json(infoCalls <= 2 ? before : after);
      }
      if (path === "/agent-market/runs/run-9") {
        runPolls++;
        return json({ run: { id: "run-9", status: runPolls < 2 ? "running" : "succeeded" }, steps: [] });
      }
      return json({}, 404);
    });
    const onChanged = vi.fn();
    render(<UpgradeSection installId="i-1" onChanged={onChanged} pollMs={1} settleMs={1} />);
    fireEvent.click(await screen.findByTestId("am-upgrade-start"));
    expect(await screen.findByTestId("am-upgrade-testing")).toBeTruthy();
    const outcome = await screen.findByTestId("am-upgrade-outcome");
    expect(outcome.getAttribute("data-outcome")).toBe("upgraded");
    expect(outcome.textContent).toBe("อัปเกรดแล้ว ตอนนี้ใช้รุ่น 1.1.0");
    expect(infoCalls).toBe(3);
    const post = raw.mock.calls.find((c) => c[0] === "/agent-market/installs/i-1/upgrade" && (c[1] as RequestInit)?.method === "POST")!;
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ definition_id: "d-2" });
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByTestId("am-upgrade-current").textContent).toContain("ใช้รุ่น 1.1.0 อยู่");
    expect(screen.getByTestId("am-upgrade-rollback").textContent).toBe("ย้อนกลับเป็นรุ่น 1.0.0");
  });

  it("a self-test that does not pass keeps the old version, and says where to read why", async () => {
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/installs/i-1/upgrade" && init?.method === "POST") return json({ run: { id: "run-9" } }, 202);
      if (path === "/agent-market/installs/i-1/upgrade") return json(before);
      if (path === "/agent-market/runs/run-9") return json({ run: { id: "run-9", status: "failed" }, steps: [] });
      return json({}, 404);
    });
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} pollMs={1} settleMs={1} />);
    fireEvent.click(await screen.findByTestId("am-upgrade-start"));
    const outcome = await screen.findByTestId("am-upgrade-outcome");
    expect(outcome.getAttribute("data-outcome")).toBe("kept");
    expect(outcome.textContent).toContain("ยังใช้รุ่นเดิม (1.0.0) รุ่น 1.1.0 ยังไม่ผ่านการทดสอบกับข้อมูลตัวอย่าง");
    expect(within(outcome).getByRole("link").getAttribute("href")).toBe("/chat/agents/i/i-1");
  });

  it("a run that passed but missed its expectations: the version did not change, so it is 'kept'", async () => {
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/installs/i-1/upgrade" && init?.method === "POST") return json({ run: { id: "run-9" } }, 202);
      if (path === "/agent-market/installs/i-1/upgrade") return json(before);
      if (path === "/agent-market/runs/run-9") return json({ run: { id: "run-9", status: "succeeded" }, steps: [] });
      return json({}, 404);
    });
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} pollMs={1} settleMs={1} />);
    fireEvent.click(await screen.findByTestId("am-upgrade-start"));
    expect((await screen.findByTestId("am-upgrade-outcome")).getAttribute("data-outcome")).toBe("kept");
  });

  it("rolls back; with nothing to roll back to (409) it says so", async () => {
    let rolled = false;
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/installs/i-1/rollback") {
        rolled = true;
        return json({ install: { id: "i-1", definition_version: "1.0.0" } });
      }
      return json(rolled ? { current: v("d-1", "1.0.0"), available: v("d-2", "1.1.0"), changes } : after);
    });
    const onChanged = vi.fn();
    render(<UpgradeSection installId="i-1" onChanged={onChanged} />);
    fireEvent.click(await screen.findByTestId("am-upgrade-rollback"));
    expect((await screen.findByTestId("am-upgrade-note")).textContent).toBe("ย้อนกลับแล้ว ตอนนี้ใช้รุ่น 1.0.0");
    expect(onChanged).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId("am-upgrade-rollback")).toBeNull());

    cleanup();
    raw.mockImplementation(async (path: string) =>
      path === "/agent-market/installs/i-1/rollback"
        ? json({ error: { code: "NO_PREVIOUS", message: "ยังไม่มีรุ่นก่อนหน้าให้ย้อนกลับ" } }, 409)
        : json(after),
    );
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("am-upgrade-rollback"));
    expect(await screen.findByText("ยังไม่มีรุ่นก่อนหน้าให้ย้อนกลับ")).toBeTruthy();
  });

  it("the latest version says so", async () => {
    raw.mockResolvedValue(json({ current: v("d-2", "1.1.0"), available: null }));
    render(<UpgradeSection installId="i-1" onChanged={vi.fn()} />);
    expect((await screen.findByTestId("am-upgrade-latest")).textContent).toBe("เป็นรุ่นล่าสุดแล้ว");
  });
});
