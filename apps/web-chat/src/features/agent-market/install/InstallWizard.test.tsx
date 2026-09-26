// AM2 — the install wizard's steps come from the manifest's install.questions.
//   * the financial report (asks "mapping", "currency") keeps AM1's five
//     steps, its theme step asks the company name and its file-set step the
//     currency and materiality;
//   * the invoice agent (asks "company_tax") gets company details → theme →
//     file set → self-test: the tax ID is checked (mod-11) before the three
//     defaults are saved, the theme step does not ask the name again, and
//     the file-set step asks no currency.
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

import type { Manifest } from "../types";
import { InstallWizard } from "./InstallWizard";
import { FormatsStep, ThemeStep } from "./steps";
import { installSteps, themeAsksCompany } from "./wizard";

// The parts of backend/agent_market/packages/*/agent.json the wizard reads.
const financial: Manifest = {
  id: "sran.financial-report",
  inputs: [
    { role: "primary", label: "บัญชีแยกประเภทของเดือนที่จะปิด", required: true, accept: [".xlsx", ".csv"] },
    { role: "budget", label: "งบประมาณของงวดนั้น", accept: [".xlsx", ".csv"] },
  ],
  outputs: [
    { format: "docx", label: "รายงาน .docx", default: true },
    { format: "pptx", label: "สไลด์ .pptx", default: true },
    { format: "xlsx", label: "ตารางตัวเลข .xlsx", default: false },
  ],
  install: { defaults: { formats: ["docx", "pptx"], currency: "THB" }, questions: ["company", "theme", "formats", "currency", "mapping"] },
};
const invoice: Manifest = {
  id: "sran.invoice-processing",
  inputs: [{ role: "documents", label: "ใบกำกับภาษี ใบเสร็จ", required: true, accept: [".pdf", ".xml", ".jpg", ".jpeg", ".png"] }],
  outputs: [{ format: "xlsx", label: "ทะเบียน .xlsx", default: true }],
  install: { defaults: { formats: ["xlsx"], currency: "THB" }, questions: ["company_tax", "theme", "formats"] },
  photo_notice: "รูปถ่ายใบเสร็จอ่านแบบพยายามที่สุด (best-effort)",
};
const catalog: Record<string, Manifest> = { "sran.financial-report": financial, "sran.invoice-processing": invoice };

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

let stored: { key: string; value: unknown; source: string; label: string; note: string; updated_at: string }[] = [];
const puts = () =>
  raw.mock.calls
    .filter((c) => (c[1] as RequestInit | undefined)?.method === "PUT")
    .map((c) => ({ path: String(c[0]), body: JSON.parse(String((c[1] as RequestInit).body)) as { value?: unknown; id?: string; company?: string } }));

function route(path: string, init?: RequestInit) {
  const method = init?.method ?? "GET";
  if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
  if (path.startsWith("/agent-market/catalog/")) {
    const slug = decodeURIComponent(path.slice("/agent-market/catalog/".length));
    return json({ slug, version: "1.0.0", name: slug, manifest: catalog[slug] });
  }
  if (path === "/agent-market/installs") {
    return method === "POST"
      ? json({ install: { id: "i-1", slug: "x", definition_version: "1.0.0", title: "x", created_at: "" }, created: true })
      : json({ installs: [] });
  }
  if (path === "/agent-market/installs/i-1/defaults") return json({ defaults: stored });
  if (path.startsWith("/agent-market/installs/i-1/defaults/") && method === "PUT") {
    const key = decodeURIComponent(path.split("/").pop()!);
    const { value } = JSON.parse(String(init?.body)) as { value: unknown };
    stored = [...stored.filter((d) => d.key !== key), { key, value, source: "settings", label: key, note: "", updated_at: "" }];
    return json({ saved: true });
  }
  if (path === "/agent-market/theme") return method === "PUT" ? json({ version: 1 }) : json({ theme: null, version: 0, presets: [{ id: "navy", name: "navy" }] });
  if (path.startsWith("/agent-market/installs/i-1/events")) return new Promise(() => {}); // the self-test's live stream: never needed here
  if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items: [], cursor: 0 });
  return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
}

beforeEach(() => {
  stored = [];
  raw.mockReset();
  raw.mockImplementation(async (path: string, init?: RequestInit) => route(path, init));
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  window.sessionStorage.clear();
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stepLabels(): (string | null)[] {
  const nav = screen.getByRole("navigation", { name: "ขั้นตอนการติดตั้ง" });
  return within(nav)
    .getAllByRole("button")
    .map((b) => b.getAttribute("aria-label"));
}

describe("installSteps", () => {
  it("financial report: AM1's five steps, unchanged", () => {
    expect(installSteps(financial)).toEqual(["source", "mapping", "theme", "formats", "selftest"]);
    expect(themeAsksCompany(financial)).toBe(true);
  });

  it("invoice agent: company details, theme, file set, self-test", () => {
    expect(installSteps(invoice)).toEqual(["company_tax", "theme", "formats", "selftest"]);
    expect(themeAsksCompany(invoice)).toBe(false);
  });

  it("a manifest that declares no questions gets AM1's full wizard", () => {
    expect(installSteps({})).toEqual(["source", "mapping", "theme", "formats", "selftest"]);
    expect(installSteps({ install: { questions: [] } })).toEqual(["source", "mapping", "theme", "formats", "selftest"]);
  });
});

describe("InstallWizard", () => {
  it("financial report: five steps, starting at the data source", async () => {
    render(<InstallWizard slug="sran.financial-report" />);
    expect(await screen.findByTestId("am-step-source")).toBeTruthy();
    expect(stepLabels()).toEqual(["เลือกแหล่งข้อมูล", "จับคู่คอลัมน์", "ธีมเอกสาร", "ชุดไฟล์", "ทดสอบกับข้อมูลตัวอย่าง"]);
    expect(screen.queryByTestId("am-step-company-tax")).toBeNull();
  });

  it("invoice agent: the tax ID is checked, three defaults saved, the name not asked again, no currency", async () => {
    render(<InstallWizard slug="sran.invoice-processing" />);
    const step = await screen.findByTestId("am-step-company-tax");
    expect(stepLabels()).toEqual(["ข้อมูลบริษัท", "ธีมเอกสาร", "ชุดไฟล์", "ทดสอบกับข้อมูลตัวอย่าง"]);
    expect(step.textContent).toContain("ใช้เพื่อตรวจว่าใบกำกับภาษีออกในนามบริษัทนี้เท่านั้น");
    const next = screen.getByTestId("am-company-tax-save") as HTMLButtonElement;
    expect(next.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("am-company-name"), { target: { value: "บริษัท ตัวอย่าง จำกัด" } });
    fireEvent.change(screen.getByTestId("am-company-tax-id"), { target: { value: "0105545009870" } });
    expect(screen.getByTestId("am-field-error").textContent).toBe("เลข 13 หลักนี้ไม่ผ่านการตรวจหลักสุดท้าย ช่วยตรวจอีกครั้ง");
    expect(next.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("am-company-tax-id"), { target: { value: "๐๑๐๕๕๔๕๐๐๙๘๗๕" } });
    expect(screen.queryByTestId("am-field-error")).toBeNull();
    expect(next.disabled).toBe(false);

    // a branch needs its 5-digit number
    fireEvent.click(screen.getByTestId("am-company-branch"));
    expect(next.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("am-company-branch-no"), { target: { value: "12" } });
    fireEvent.blur(screen.getByTestId("am-company-branch-no"));
    expect(screen.getByTestId("am-field-error").textContent).toBe("เลขที่สาขาต้องมี 5 หลัก เช่น 00001");
    fireEvent.change(screen.getByTestId("am-company-branch-no"), { target: { value: "00012" } });
    expect(next.disabled).toBe(false);

    fireEvent.click(next);
    const theme = await screen.findByTestId("am-step-theme");
    expect(puts()).toEqual([
      { path: "/agent-market/installs/i-1/defaults/company", body: { value: "บริษัท ตัวอย่าง จำกัด" } },
      { path: "/agent-market/installs/i-1/defaults/company_tax_id", body: { value: "0105545009875" } },
      { path: "/agent-market/installs/i-1/defaults/company_branch", body: { value: "00012" } },
    ]);

    // the theme step does not ask the name a second time
    expect(within(theme).queryAllByRole("textbox")).toHaveLength(0);
    // the theme step's own read of the defaults (the second one) has landed
    await screen.findAllByTestId("am-theme-preset");
    await waitFor(() => expect(raw.mock.calls.filter((c) => c[0] === "/agent-market/installs/i-1/defaults")).toHaveLength(2));
    await new Promise((r) => setTimeout(r, 0));
    fireEvent.click(screen.getByTestId("am-theme-save"));
    await screen.findByTestId("am-step-formats");
    const after = puts().slice(3);
    expect(after.map((p) => p.path)).toEqual(["/agent-market/theme", "/agent-market/installs/i-1/defaults/theme"]);
    expect(after[0]!.body).toEqual({ id: "navy", company: "บริษัท ตัวอย่าง จำกัด" });

    // the file set asks no currency or materiality
    expect(screen.queryByTestId("am-formats-currency")).toBeNull();
    fireEvent.click(screen.getByTestId("am-formats-save"));
    await screen.findByTestId("am-step-selftest");
    expect(puts().slice(5)).toEqual([{ path: "/agent-market/installs/i-1/defaults/formats", body: { value: ["xlsx"] } }]);
  });

  it("invoice agent: what was saved before fills the company step", async () => {
    stored = [
      { key: "company", value: "บริษัท เดิม จำกัด", source: "settings", label: "", note: "", updated_at: "" },
      { key: "company_tax_id", value: "0105563012343", source: "settings", label: "", note: "", updated_at: "" },
      { key: "company_branch", value: "00003", source: "settings", label: "", note: "", updated_at: "" },
    ];
    render(<InstallWizard slug="sran.invoice-processing" />);
    await screen.findByTestId("am-step-company-tax");
    await waitFor(() => expect((screen.getByTestId("am-company-tax-id") as HTMLInputElement).value).toBe("0105563012343"));
    expect((screen.getByTestId("am-company-name") as HTMLInputElement).value).toBe("บริษัท เดิม จำกัด");
    expect((screen.getByTestId("am-company-branch") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("am-company-branch-no") as HTMLInputElement).value).toBe("00003");
  });
});

describe("steps on their own", () => {
  it("financial file set keeps currency and materiality, and saves them", async () => {
    const onNext = vi.fn();
    render(<FormatsStep installId="i-1" manifest={financial} onBack={vi.fn()} onNext={onNext} />);
    expect(screen.getByTestId("am-formats-currency")).toBeTruthy();
    fireEvent.click(screen.getByTestId("am-formats-save"));
    await waitFor(() => expect(onNext).toHaveBeenCalled());
    expect(puts().map((p) => p.path)).toEqual(["/agent-market/installs/i-1/defaults/formats", "/agent-market/installs/i-1/defaults/currency"]);
  });

  it("financial theme step still asks the company name", () => {
    render(<ThemeStep installId="i-1" onBack={vi.fn()} onNext={vi.fn()} />);
    expect(within(screen.getByTestId("am-step-theme")).getAllByRole("textbox")).toHaveLength(1);
  });
});
