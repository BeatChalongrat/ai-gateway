// AM15 — a run that waits for its model does not say it waits for the person.
//
// Every waiting run was titled "รอคำตอบของคุณ". A run that stopped because the
// model it may use cannot be reached asks nothing: a person reading that title
// looks for a question that is not there. It is titled "รอโมเดล" / "Waiting for
// the model"; a run that did ask keeps the title it had.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import { MODEL_UNREACHABLE, outcomeKey, outcomeTone } from "../outcome";
import type { ProgressPayload } from "../types";
import { ProgressCard } from "./ProgressCard";

const waiting = (reason: string): ProgressPayload => ({
  run_id: "run-1",
  kind: "run",
  status: "awaiting_input",
  outcome: "awaiting_input",
  reason,
  steps: [
    { key: "parse", label: "อ่านไฟล์ 2 ไฟล์", status: "done" },
    { key: "compute", label: "คำนวณด้วยสคริปต์", status: "done" },
    { key: "instructions", label: "อ่านคำขอที่พิมพ์มา", status: "failed" },
  ],
});
const title = () => screen.getByTestId("am-progress-title").textContent;

beforeEach(() => {
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

describe("a run that waits for its model", () => {
  it("has its own words, and only for that reason", () => {
    expect(MODEL_UNREACHABLE).toBe("model_unreachable"); // the runner's own code (agentmarket/modeldown.go)
    expect(outcomeKey("awaiting_input", "model_unreachable")).toBe("progress.title.waiting_model");
    for (const reason of ["waiting_preflight", "waiting_postcompute", "reask", "budget_install", "", undefined, null]) {
      expect(outcomeKey("awaiting_input", reason)).toBe("progress.title.awaiting_input");
    }
    // a run that ended for that reason is a failed run, said as one
    expect(outcomeKey("failed", "model_unreachable")).toBe("progress.title.failed");
    expect(outcomeTone("awaiting_input")).toBe("warning");
  });

  it("is titled รอโมเดล on its card, not รอคำตอบของคุณ", () => {
    render(<ProgressCard progress={waiting("model_unreachable")} />);
    expect(title()).toBe("รอโมเดล");
    expect(screen.queryByText("รอคำตอบของคุณ")).toBeNull();
  });

  it("is titled in English on an English screen", () => {
    useI18nStore.setState({ locale: "en" });
    render(<ProgressCard progress={waiting("model_unreachable")} />);
    expect(title()).toBe("Waiting for the model");
  });

  it("a run that asked a question still waits for the person's answer", () => {
    render(<ProgressCard progress={waiting("waiting_preflight")} />);
    expect(title()).toBe("รอคำตอบของคุณ");
  });
});
