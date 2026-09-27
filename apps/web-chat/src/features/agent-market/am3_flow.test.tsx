// AM3 — using an agent without a builder next to you:
//   * every install step says why it asks and what happens if it is wrong;
//     an agent that fills company forms gets an optional, skippable step;
//   * the self-test ends on the ACTUAL files (or, when it fails, on what to
//     do, that nothing of yours was touched, and "แจ้งปัญหา");
//   * "แจ้งปัญหา" sends one sentence with the page, and shows the reference;
//   * the budget banner and the "ลองต่อ" of a run waiting for budget;
//   * sign-off from a finished run, and the outputs card's reviewed badge.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import { amDictionaries } from "./i18n";
import { SelfTestStep } from "./install/steps";
import { installSteps, STEP_WHY, type StepId } from "./install/wizard";
import { ProblemButton } from "./problem/ProblemButton";
import { SignOffButton } from "./review/SignOff";
import type { Manifest, OutputsPayload, ThreadItem } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { BudgetBanner, tightest } from "./workspace/BudgetBanner";
import { WorkspaceContext } from "./workspace/context";
import { OutputsCard } from "./workspace/OutputsCard";
import { ThreadView } from "./workspace/ThreadView";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const calls = (path: string) => raw.mock.calls.filter((c) => c[0] === path);
const bodyOf = (path: string) => JSON.parse(String((calls(path)[0]![1] as RequestInit).body));

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
    if (path.startsWith("/agent-market/installs/i-1/events")) return new Promise(() => {});
    return handler(path, init);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const withTemplates: Manifest = {
  install: { questions: ["company", "theme", "formats", "currency", "mapping"] },
  templates: { fields: { company: { label: "ชื่อบริษัท" } } },
};

describe("the install wizard, for someone alone", () => {
  it("every step there is has a why and an if-wrong line, in Thai and English", () => {
    const steps: StepId[] = ["source", "mapping", "company_tax", "policy", "theme", "formats", "template", "selftest"];
    for (const s of steps) {
      for (const key of [STEP_WHY[s].why, STEP_WHY[s].ifWrong]) {
        expect(amDictionaries.th[key].length).toBeGreaterThan(20);
        expect(amDictionaries.en[key].length).toBeGreaterThan(20);
      }
    }
  });

  it("an agent that fills company forms gets the optional form step before the self-test", () => {
    expect(installSteps(withTemplates)).toEqual(["source", "mapping", "theme", "formats", "template", "selftest"]);
    expect(installSteps({ install: { questions: ["company_tax", "theme", "formats"] } })).not.toContain("template");
  });

  const selfTestThread = (status: string): ThreadItem[] => [
    {
      id: "p-1",
      install_id: "i-1",
      run_id: "r-1",
      kind: "progress",
      payload: { run_id: "r-1", kind: "self_test", status, reason: status === "failed" ? "compute_failed" : "" },
      rev: 1,
      created_at: "",
    },
  ];

  it("the self-test ends on the actual files it made", async () => {
    handler = (path) => {
      if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", self_test_run_id: "r-1", slug: "x", title: "x", created_at: "", definition_version: "1" }, manifest: {}, defaults: [], budget: { credits: 1, spent_this_month: 0 } });
      if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items: selfTestThread("succeeded"), cursor: 1 });
      if (path === "/agent-market/runs/r-1")
        return json({
          run: { id: "r-1", status: "succeeded", kind: "self_test" },
          artifacts: [
            { id: "a-1", format: "docx", name: "financial_2026-09_report_v1.docx", version: 1, size: 1000, created_at: "" },
            { id: "a-2", format: "pptx", name: "financial_2026-09_deck_v1.pptx", version: 1, size: 2000, created_at: "" },
          ],
        });
      return json({}, 404);
    };
    render(<SelfTestStep installId="i-1" manifest={{}} onBack={vi.fn()} onFinish={vi.fn()} />);
    const panel = await screen.findByTestId("am-selftest-files");
    expect(panel.textContent).toContain("ไฟล์จริงที่ agent ทำจากข้อมูลตัวอย่าง");
    await waitFor(() => expect(within(panel).getAllByTestId("am-artifact")).toHaveLength(2));
    expect(panel.textContent).toContain("financial_2026-09_deck_v1.pptx");
  });

  it("a failed self-test says what to do, that nothing of yours was touched, and offers a report", async () => {
    handler = (path) => {
      if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", self_test_run_id: "r-1", slug: "x", title: "x", created_at: "", definition_version: "1" }, manifest: {}, defaults: [], budget: { credits: 1, spent_this_month: 0 } });
      if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items: selfTestThread("failed"), cursor: 1 });
      return json({}, 404);
    };
    render(<SelfTestStep installId="i-1" manifest={{}} onBack={vi.fn()} onFinish={vi.fn()} />);
    const failed = await screen.findByTestId("am-selftest-failed");
    expect(failed.textContent).toContain("ไฟล์และข้อมูลของคุณไม่ได้ถูกใช้หรือเปลี่ยนแปลง");
    expect(within(failed).getByTestId("am-problem-open")).toBeTruthy();
    expect(within(failed).getByTestId("am-selftest-again")).toBeTruthy();
  });
});

describe("report a problem", () => {
  it("one sentence, with the page, and the reference shown back", async () => {
    handler = (path) =>
      path === "/agent-market/runs/r-9/problem"
        ? json({ report: { id: "p", ref: "AB12CD" }, message: "ส่งเรื่องถึงผู้ดูแล Agent Market แล้ว (เลขอ้างอิง #AB12CD)" })
        : json({}, 404);
    render(<ProblemButton target={{ kind: "run", runId: "r-9" }} />);
    fireEvent.click(screen.getByTestId("am-problem-open"));
    fireEvent.click(screen.getByTestId("am-problem-send"));
    expect(screen.getByRole("alert").textContent).toContain("เขียนสั้น ๆ หนึ่งประโยค");
    expect(calls("/agent-market/runs/r-9/problem")).toHaveLength(0);
    fireEvent.change(screen.getByTestId("am-problem-text"), { target: { value: "ยอดขายในรายงานไม่ตรงกับ GL" } });
    fireEvent.click(screen.getByTestId("am-problem-send"));
    expect((await screen.findByTestId("am-problem-done")).textContent).toContain("#AB12CD");
    const body = bodyOf("/agent-market/runs/r-9/problem");
    expect(body.text).toBe("ยอดขายในรายงานไม่ตรงกับ GL");
    expect(typeof body.page).toBe("string");
  });
});

describe("budgets", () => {
  const base = { user_limit: 1000, user_spent: 0, user_own_limit: false, department_spent: 0, install_limit: 3000000, install_spent: 0, exhausted: "", contact: { name: "ฝ่ายไอที", email: "it@x.test" } };

  it("nothing while there is room; a note under a tenth; a clear one naming whom to ask when used up", async () => {
    expect(tightest({ ...base, user_spent: 950 })?.which).toBe("user");
    handler = () => json({ budget: { ...base, user_spent: 100 }, remaining: 900 });
    const { unmount } = render(<BudgetBanner installId="i-1" />);
    await waitFor(() => expect(calls("/agent-market/installs/i-1/budget")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId("am-budget-banner")).toBeNull();
    unmount();

    handler = () => json({ budget: { ...base, user_spent: 950 }, remaining: 50 });
    const low = render(<BudgetBanner installId="i-1" />);
    expect((await screen.findByTestId("am-budget-banner")).getAttribute("data-state")).toBe("low");
    expect(screen.getByTestId("am-budget-banner").textContent).toContain("เหลือ 50 เครดิต");
    low.unmount();

    handler = () => json({ budget: { ...base, department: "บัญชี", department_limit: 5000, department_spent: 5200, exhausted: "department", contact: { name: "หัวหน้าบัญชี", email: "" } }, remaining: -200 });
    render(<BudgetBanner installId="i-1" />);
    const out = await screen.findByTestId("am-budget-banner");
    expect(out.getAttribute("data-state")).toBe("out");
    expect(out.textContent).toContain("งบเครดิตของแผนกบัญชีเดือนนี้ใช้หมดแล้ว");
    expect(out.textContent).toContain("หัวหน้าบัญชี");
    expect(out.textContent).toContain("ยังอยู่ครบ");
  });

  it("a run waiting for budget: ลองต่อ, and the server's answer when it is still out", async () => {
    let still = true;
    handler = (path) =>
      path === "/agent-market/runs/r-7/resume"
        ? still
          ? json({ error: { code: "BUDGET_STILL_EXHAUSTED", message: "งบเครดิตของคุณเดือนนี้ยังหมดอยู่ — ขอเพิ่มได้จาก ฝ่ายไอที" } }, 409)
          : json({ resumed: true })
        : json({}, 404);
    const items: ThreadItem[] = [
      {
        id: "n-1",
        install_id: "i-1",
        run_id: "r-7",
        kind: "notice",
        payload: { text: "หยุดรอไว้ก่อนครับ: งบเครดิตของคุณเดือนนี้ใช้หมดแล้ว", tone: "warning", action: "resume_budget", run_id: "r-7" },
        rev: 1,
        created_at: "",
      },
    ];
    render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: vi.fn() }}>
        <ThreadView items={items} />
      </WorkspaceContext.Provider>,
    );
    fireEvent.click(screen.getByTestId("am-resume-budget"));
    expect((await screen.findByTestId("am-resume-note")).textContent).toContain("ยังหมดอยู่");
    still = false;
    fireEvent.click(screen.getByTestId("am-resume-budget"));
    await waitFor(() => expect(screen.getByTestId("am-resume-note").textContent).toBe("ทำต่อจากจุดเดิมแล้ว"));
  });
});

describe("sign-off", () => {
  it("the dialog sends the name and the note, and shows a refusal as the server wrote it", async () => {
    let refuse = true;
    handler = (path) =>
      path === "/agent-market/runs/r-1/review"
        ? refuse
          ? json({ error: { code: "NOT_REVIEWABLE", message: "ยังตรวจทานรอบนี้ไม่ได้ เพราะยังมีข้อยกเว้นที่รอคนตัดสิน 2 รายการ — ตัดสินข้อยกเว้นให้ครบก่อน" } }, 409)
          : json({ review: { id: "v-1", run_id: "r-1", reviewer_name: "สมชาย ผู้ตรวจ", reviewer_email: "s@x.test", note: "", created_at: "" }, job: { id: "j" } })
        : json({}, 404);
    const onDone = vi.fn();
    render(<SignOffButton runId="r-1" onDone={onDone} />);
    fireEvent.click(screen.getByTestId("am-signoff-open"));
    fireEvent.change(screen.getByTestId("am-signoff-name"), { target: { value: "สมชาย ผู้ตรวจ" } });
    fireEvent.change(screen.getByTestId("am-signoff-note"), { target: { value: "ตรวจกับงบทดลองแล้ว" } });
    fireEvent.click(screen.getByTestId("am-signoff-send"));
    expect((await screen.findByRole("alert")).textContent).toContain("ข้อยกเว้นที่รอคนตัดสิน 2 รายการ");
    refuse = false;
    fireEvent.click(screen.getByTestId("am-signoff-send"));
    expect((await screen.findByTestId("am-signoff-done")).textContent).toContain("สมชาย ผู้ตรวจ");
    expect(onDone).toHaveBeenCalled();
    expect(JSON.parse(String((calls("/agent-market/runs/r-1/review")[1]![1] as RequestInit).body))).toEqual({ name: "สมชาย ผู้ตรวจ", note: "ตรวจกับงบทดลองแล้ว" });
  });

  it("the outputs card: the reviewed badge, and sign-off offered only on a real run", () => {
    const reviewed: OutputsPayload = {
      run_id: "r-1",
      artifacts: [{ id: "a", name: "report_v2_reviewed.docx", format: "docx", size: 10, version: 2 }],
      rerender: true,
      review: { id: "v", reviewer: "สมชาย ผู้ตรวจ", at_label: "28 ก.ย. 2569 เวลา 10:05 น.", note: "ตรวจแล้ว", skipped: ["tpl.xlsx"] },
    };
    const { unmount } = render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: vi.fn() }}>
        <OutputsCard outputs={reviewed} />
      </WorkspaceContext.Provider>,
    );
    const badge = screen.getByTestId("am-outputs-review");
    expect(badge.textContent).toContain("ฉบับตรวจทานแล้วโดย สมชาย ผู้ตรวจ · 28 ก.ย. 2569 เวลา 10:05 น.");
    expect(badge.textContent).toContain("tpl.xlsx");
    expect(screen.queryByTestId("am-signoff-open")).toBeNull(); // already signed
    unmount();

    const plain: OutputsPayload = { run_id: "r-2", artifacts: [] };
    const run = render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: vi.fn() }}>
        <OutputsCard outputs={plain} />
      </WorkspaceContext.Provider>,
    );
    expect(screen.getByTestId("am-signoff-open")).toBeTruthy();
    run.unmount();
    render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: vi.fn(), selfTestRuns: new Set(["r-2"]) }}>
        <OutputsCard outputs={plain} />
      </WorkspaceContext.Provider>,
    );
    expect(screen.queryByTestId("am-signoff-open")).toBeNull();
  });
});

describe("the wizard and the run page, rendered", () => {
  it("the wizard shows the step's why and if-wrong lines", async () => {
    const { InstallWizard } = await import("./install/InstallWizard");
    handler = (path, init) => {
      if (path === "/agent-market/catalog/sran.financial-report")
        return json({ slug: "sran.financial-report", version: "1.2.0", name: "รายงานการเงิน", manifest: { install: { questions: ["company", "theme", "formats", "currency", "mapping"] } } });
      if (path === "/agent-market/installs")
        return init?.method === "POST" ? json({ install: { id: "i-1", slug: "x", definition_version: "1", title: "x", created_at: "" }, created: true }) : json({ installs: [] });
      return json({}, 404);
    };
    window.sessionStorage.clear();
    render(<InstallWizard slug="sran.financial-report" />);
    const why = await screen.findByTestId("am-step-why");
    expect(why.getAttribute("data-step")).toBe("source");
    expect(why.textContent).toContain("ทำไมถามข้อนี้:");
    expect(why.textContent).toContain("ถ้าตอบผิดหรือข้ามไป:");
  });

  it("a finished run offers sign-off, sharing and a problem report", async () => {
    const { RunDetailPage } = await import("./runs/RunDetailPage");
    handler = (path) => {
      if (path === "/agent-market/runs/r-1")
        return json({
          run: { id: "r-1", install_id: "i-1", kind: "run", status: "succeeded", created_at: "2026-09-28T00:00:00Z", started_at: "2026-09-28T00:00:00Z", finished_at: "2026-09-28T00:01:00Z" },
          steps: [],
          clarifications: [],
          artifacts: [],
          files: [],
          children: [],
          install: { id: "i-1", title: "ปิดงบการเงิน", slug: "sran.financial-report" },
          budget: { credits: 1, spent_this_month: 0 },
        });
      if (path === "/agent-market/runs/r-1/reviews")
        return json({ reviews: [{ id: "v", run_id: "r-1", reviewer_email: "s@x.test", reviewer_name: "สมชาย", note: "", created_at: "2026-09-28T01:00:00Z", stamp_status: "succeeded" }] });
      if (path === "/agent-market/runs/r-1/exceptions") return json({ exceptions: [] });
      return json({}, 404);
    };
    render(<RunDetailPage runId="r-1" />);
    const actions = await screen.findByTestId("am-run-actions");
    expect(within(actions).getByTestId("am-signoff-open")).toBeTruthy();
    expect(within(actions).getByTestId("am-share-open")).toBeTruthy();
    expect(within(actions).getByTestId("am-problem-open")).toBeTruthy();
    expect((await screen.findByTestId("am-review")).textContent).toContain("ใส่ชื่อในไฟล์แล้ว");
  });
});
