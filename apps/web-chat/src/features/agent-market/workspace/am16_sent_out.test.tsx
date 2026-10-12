// AM16 — a run says on the face of its card when its documents were sent to a
// model outside the organisation as they were typed.
//
// Masking is the admin's own switch per agent now, off by default: a run whose
// documents may leave sends them out unmasked unless an admin turned masking on.
// The owner's condition for that: "the run says it plainly, on its own card, in
// both languages … on the face of it, not in details." These tests hold the
// sentence, where it stands, and — the refusal — that a run which sent nothing
// out never shows it.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";

import type { OutputsModel, OutputsPayload, ProgressPayload } from "../types";
import { WorkspaceContext, type WorkspaceCtx } from "./context";
import { OutputsCard, SeenBy } from "./OutputsCard";
import { ProgressCard } from "./ProgressCard";

const TH = "เอกสารของรอบนี้ถูกส่งออกนอกองค์กร และไม่ได้ปิดบังข้อมูล";
const EN = "This run's documents were sent outside the organisation and were not masked";

const ws: WorkspaceCtx = { installId: "i-1", merge: () => {}, threadId: null };
const inWorkspace = (node: React.ReactNode) => render(<WorkspaceContext.Provider value={ws}>{node}</WorkspaceContext.Provider>);
const progress = (over: Partial<ProgressPayload>): ProgressPayload => ({
  run_id: "run-1",
  kind: "run",
  status: "running",
  outcome: "running",
  steps: [
    { key: "parse", label: "อ่านไฟล์ 1 ไฟล์", status: "done" },
    { key: "vision", label: "อ่านสแกนด้วยโมเดล", status: "done" },
  ],
  ...over,
});
const outputs = (model: OutputsModel): OutputsPayload => ({ run_id: "run-1", artifacts: [], model });
const note = () => screen.queryByTestId("am-sent-out-unmasked");

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  // the files card's dialogs read matchMedia; jsdom has none
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the run's own card", () => {
  it("says it as soon as it has happened — while the run is still going", () => {
    render(<ProgressCard progress={progress({ sent_out_unmasked: true })} />);
    expect(note()?.textContent).toContain(TH);
    // on the card itself, not behind anything a person has to open
    expect(screen.getByTestId("am-progress").contains(note())).toBe(true);
    expect(note()?.closest("details")).toBeNull();
  });

  it("says it whatever the run came to: waiting for its model, failed, finished", () => {
    for (const over of [
      { status: "awaiting_input", outcome: "awaiting_input", reason: "model_unreachable" },
      { status: "failed", outcome: "failed", reason: "step_failed" },
      { status: "succeeded", outcome: "succeeded" },
    ] as Partial<ProgressPayload>[]) {
      const { unmount } = render(<ProgressCard progress={progress({ ...over, sent_out_unmasked: true })} />);
      expect(note()?.textContent, JSON.stringify(over)).toContain(TH);
      unmount();
    }
  });

  it("a run that sent nothing out never says it", () => {
    for (const p of [progress({}), progress({ sent_out_unmasked: false }), progress({ status: "succeeded", outcome: "succeeded" })]) {
      const { unmount } = render(<ProgressCard progress={p} />);
      expect(note()).toBeNull();
      expect(screen.getByTestId("am-progress").textContent).not.toContain("นอกองค์กร");
      unmount();
    }
  });

  it("in English on an English screen, with no Thai in it", () => {
    useI18nStore.setState({ locale: "en" });
    render(<ProgressCard progress={progress({ sent_out_unmasked: true })} />);
    expect(note()?.textContent).toContain(EN);
    expect(note()?.textContent).not.toMatch(/[฀-๿]/);
  });
});

describe("the card of the files", () => {
  it("says it on its face — outside the folded details — in both languages", () => {
    inWorkspace(<OutputsCard outputs={outputs({ uses: "vision", choice: "google/gemini-2.5-flash", vision_model: "google/gemini-2.5-flash", sent_out_unmasked: true })} />);
    expect(note()?.textContent).toContain(TH);
    const details = screen.queryByTestId("am-outputs-details");
    const folded = details?.getAttribute("aria-controls");
    if (folded) expect(document.getElementById(folded)?.contains(note()) ?? false).toBe(false);
    cleanup();
    useI18nStore.setState({ locale: "en" });
    inWorkspace(<OutputsCard outputs={outputs({ uses: "vision", sent_out_unmasked: true })} />);
    expect(note()?.textContent).toContain(EN);
  });

  it("does not say it for a run whose documents stayed inside or were masked", () => {
    for (const model of [{ uses: "vision" }, { uses: "text", masking: "off" }, { uses: "text", masking: "on", model: "deepseek-v4-flash" }] as OutputsModel[]) {
      const { unmount } = inWorkspace(<OutputsCard outputs={outputs(model)} />);
      expect(note()).toBeNull();
      unmount();
    }
  });

  it("the masking line says 'only to a model in the organisation' only when nothing was sent out", () => {
    const lines = () => Array.from(screen.getByTestId("am-outputs-seen-by").querySelectorAll("p")).map((p) => p.textContent ?? "");
    render(<SeenBy outputs={outputs({ uses: "text", model: "qwen3.8-27b-fp8", self_hosted: true, masking: "off" })} />);
    expect(lines()).toContain("สิ่งที่ส่งให้โมเดลในรอบนี้ไม่ถูกปิดบัง — ส่งให้โมเดลในองค์กรเท่านั้น");
    cleanup();
    render(<SeenBy outputs={outputs({ uses: "text", model: "deepseek-v4-flash", self_hosted: false, masking: "off", sent_out_unmasked: true })} />);
    expect(lines()).toContain("สิ่งที่ส่งให้โมเดลในรอบนี้ไม่ถูกปิดบัง");
    expect(screen.getByTestId("am-outputs-seen-by").textContent).not.toContain("ในองค์กรเท่านั้น");
    cleanup();
    // masked text, and a page that went out as an image all the same (a run from before this card): the
    // line speaks of text only, and the face of the card says the rest
    inWorkspace(<OutputsCard outputs={outputs({ uses: "vision", masking: "on", sent_out_unmasked: true })} />);
    expect(note()?.textContent).toContain(TH);
    expect(screen.getByTestId("am-outputs-seen-by").textContent).toContain("ข้อมูลส่วนบุคคลในข้อความที่ส่งให้โมเดลในรอบนี้ถูกปิดบังก่อนส่ง");
  });
});
