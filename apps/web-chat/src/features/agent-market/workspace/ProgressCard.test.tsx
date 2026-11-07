// AM1 — the inline progress card: title per run status, step icons, elapsed
// time, and "ยกเลิก" only while the run can still be stopped.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import type { ProgressPayload } from "../types";
import { ProgressCard } from "./ProgressCard";

const base: ProgressPayload = {
  run_id: "run-1",
  kind: "run",
  status: "running",
  steps: [
    { key: "parse", label: "อ่านไฟล์ 3 ไฟล์", status: "done", detail: "GL.xlsx" },
    { key: "preflight", label: "ตรวจความครบถ้วน", status: "done", detail: "1,284 แถว" },
    { key: "compute", label: "คำนวณด้วยสคริปต์", status: "running" },
    { key: "render", label: "สร้างรายงานและสไลด์", status: "pending" },
  ],
};

beforeEach(() => {
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

const title = () => screen.getByTestId("am-progress-title").textContent;

describe("ProgressCard", () => {
  it.each([
    ["queued", "รอเริ่มงาน", true],
    ["running", "กำลังทำงาน", true],
    ["awaiting_input", "รอคำตอบของคุณ", true],
    ["succeeded", "เสร็จแล้ว", false],
    ["failed", "ไม่สำเร็จ", false],
    ["cancelled", "ยกเลิกแล้ว", false],
  ])("%s → “%s”, cancel offered: %s", (status, label, cancellable) => {
    render(<ProgressCard progress={{ ...base, status }} />);
    expect(title()).toBe(label);
    expect(!!screen.queryByTestId("am-progress-cancel")).toBe(cancellable);
    expect(screen.getByText("ดูทุกขั้นตอน").closest("a")?.getAttribute("href")).toBe("/chat/agents/runs/run-1");
  });

  it("renders each step with its status (icon + a spoken status)", () => {
    render(<ProgressCard progress={base} />);
    const items = [...document.querySelectorAll("[data-step-status]")];
    expect(items.map((i) => i.getAttribute("data-step-status"))).toEqual(["done", "done", "running", "pending"]);
    expect(items[0]!.textContent).toContain("อ่านไฟล์ 3 ไฟล์ · GL.xlsx");
    expect(items[2]!.textContent).toContain("(กำลังทำ)");
  });

  it("a finished run shows its elapsed time", () => {
    render(<ProgressCard progress={{ ...base, status: "succeeded", elapsed_ms: 134_000 }} />);
    expect(screen.getByTestId("am-progress-elapsed").textContent).toBe("2 นาที 14 วินาที");
  });

  it("a self-test says so", () => {
    render(<ProgressCard progress={{ ...base, kind: "self_test" }} />);
    expect(screen.getByTestId("am-progress").textContent).toContain("ทดสอบกับข้อมูลตัวอย่าง");
  });

  it("cancel posts to /runs/{id}/cancel", async () => {
    raw.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ run: { id: "run-1", status: "cancelled" } }) });
    render(<ProgressCard progress={base} />);
    fireEvent.click(screen.getByTestId("am-progress-cancel"));
    await waitFor(() => expect(raw).toHaveBeenCalledWith("/agent-market/runs/run-1/cancel", expect.objectContaining({ method: "POST" })));
  });
});
