// AM10 E — a form dropped into a round, for that round only.
//   * the composer of an agent that fills forms takes a form's kinds beside its
//     inputs; a file that can only be a form is one, a file that could be data
//     is data until the person marks it; the message says which (form_file_ids);
//   * an agent that fills no forms offers none of it;
//   * the form's thread item: being read · needs mapping · filled · filled short
//     (every slot it lacks named — a partly filled form is not a pass);
//   * "จับคู่ช่อง" opens the install's mapping screen, saving to the ROUND
//     (PUT /runs/{id}/template), never to the install.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, upload } = vi.hoisted(() => ({ raw: vi.fn(), upload: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("../api", async (importOriginal) => {
  const m = await importOriginal<typeof import("../api")>();
  return { ...m, amApi: { ...m.amApi, upload } };
});

import { useI18nStore } from "@/lib/i18n";

import { proposal, slots, spec } from "../template/testData";
import type { FormPayload } from "../types";
import { Composer } from "./Composer";
import { FormCard, inspectOf } from "./FormCard";
import { groupItems, ThreadView } from "./ThreadView";
import { FORM_ACCEPT, formChoice } from "./useUploads";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const file = (name: string) => new File(["x"], name);
const attach = (...names: string[]) => fireEvent.change(screen.getByTestId("am-composer-file"), { target: { files: names.map(file) } });
const posted = () => {
  const call = raw.mock.calls.find((c) => c[0] === "/agent-market/installs/i-1/messages");
  expect(call, "no message sent").toBeTruthy();
  return JSON.parse(String((call![1] as RequestInit).body));
};

beforeEach(() => {
  raw.mockReset();
  upload.mockReset();
  upload.mockImplementation(async (_i: string, f: File) => ({ id: `f-${f.name}`, name: f.name, size: f.size }));
  raw.mockImplementation(async (path: string) => {
    if (path === "/agent-market/installs/i-1/messages") return json({ item: { id: "m", install_id: "i-1", kind: "user_files", payload: {}, rev: 1, created_at: "" }, route: "form" });
    return json({}, 404);
  });
  useI18nStore.setState({ locale: "th" });
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("which staged file is a form", () => {
  it("a form's kind the agent does not read as input can only be a form; an input kind is data until marked", () => {
    expect(formChoice("ฟอร์มสรุป.docx", [".xlsx", ".csv"], FORM_ACCEPT)).toEqual({ can: true, byDefault: true });
    expect(formChoice("GL_09.xlsx", [".xlsx", ".csv"], FORM_ACCEPT)).toEqual({ can: true, byDefault: false });
    expect(formChoice("scan.pdf", [".pdf"], FORM_ACCEPT)).toEqual({ can: false, byDefault: false });
    expect(formChoice("ฟอร์ม.docx", [".pdf"], [])).toEqual({ can: false, byDefault: false });
  });
});

describe("the composer of an agent that fills forms", () => {
  it("takes a form beside the data and says which is which", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx", ".csv"]} formAccept={FORM_ACCEPT} />);
    expect(screen.getByTestId("am-composer-file").getAttribute("accept")).toBe(".xlsx,.csv,.docx,.xlsm,.pptx");
    attach("GL_09.xlsx", "ฟอร์มสรุป.docx");
    const marks = await screen.findAllByTestId("am-composer-form-mark");
    const mark = (name: string) => marks.find((m) => m.getAttribute("data-name") === name) as HTMLInputElement;
    expect(mark("ฟอร์มสรุป.docx").checked).toBe(true); // it can only be a form
    expect(mark("GL_09.xlsx").checked).toBe(false); // data, unless the person says so
    const send = screen.getByTestId("am-composer-send") as HTMLButtonElement;
    await waitFor(() => expect(send.disabled).toBe(false));
    fireEvent.click(send);
    await waitFor(() => expect(posted()).toBeTruthy());
    expect(posted().file_ids).toEqual(["f-GL_09.xlsx", "f-ฟอร์มสรุป.docx"]);
    expect(posted().form_file_ids).toEqual(["f-ฟอร์มสรุป.docx"]);
  });

  it("a workbook the person marks is sent as the form", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx", ".csv"]} formAccept={FORM_ACCEPT} />);
    attach("แบบฟอร์มของบริษัท.xlsx");
    fireEvent.click(await screen.findByTestId("am-composer-form-mark"));
    const send = screen.getByTestId("am-composer-send") as HTMLButtonElement;
    await waitFor(() => expect(send.disabled).toBe(false));
    fireEvent.click(send);
    await waitFor(() => expect(posted().form_file_ids).toEqual(["f-แบบฟอร์มของบริษัท.xlsx"]));
  });

  it("an agent that fills no forms offers no mark and refuses a .docx as before", async () => {
    render(<Composer installId="i-1" onSent={vi.fn()} accept={[".xlsx", ".csv"]} />);
    expect(screen.getByTestId("am-composer-file").getAttribute("accept")).toBe(".xlsx,.csv");
    attach("GL_09.xlsx", "ฟอร์ม.docx");
    await waitFor(() => expect(screen.getAllByTestId("am-staged-chip")).toHaveLength(2));
    expect(screen.queryByTestId("am-composer-forms")).toBeNull();
    expect(screen.getAllByTestId("am-staged-chip").find((c) => c.getAttribute("data-name") === "ฟอร์ม.docx")!.getAttribute("data-status")).toBe("error");
  });
});

const form = (status: string, extra: Partial<FormPayload> = {}): FormPayload => ({ run_id: "r-1", file_id: "f-9", name: "ฟอร์มสรุป.docx", status, open: 0, unfilled: [], ...extra });

describe("the thread draws a form's item", () => {
  // The card was tested on its own and the thread never drew it: the kind was
  // missing from the list of kinds the thread lets through, so a form dropped
  // into a round was read and filled and the person saw nothing of it (the walk
  // on the built app found it — no card, no "จับคู่ช่อง", no download).
  const sent = { id: "u-1", install_id: "i-1", kind: "user_files", payload: { text: "ใส่ในฟอร์มนี้ให้หน่อย", files: [] }, rev: 1, created_at: "2026-10-05T10:00:00Z" };
  const item = { id: "fm-1", install_id: "i-1", run_id: "r-1", kind: "form", payload: form("needs_mapping", { open: 1 }), rev: 2, created_at: "2026-10-05T10:00:01Z" };

  it("keeps the item when the thread is grouped", () => {
    expect(groupItems([sent, item]).flatMap((g) => g.items.map((i) => i.kind))).toEqual(["user_files", "form"]);
  });

  it("shows its card, with the way to map it", () => {
    render(<ThreadView items={[sent, item]} />);
    expect(screen.getByTestId("am-form").getAttribute("data-status")).toBe("needs_mapping");
    expect(screen.getByTestId("am-form-map")).toBeTruthy();
  });
});

describe("the form's card in the thread", () => {
  it("says what is happening, and that it is this round's only", () => {
    render(<FormCard p={form("inspecting")} />);
    expect(screen.getByTestId("am-form").getAttribute("data-status")).toBe("inspecting");
    expect(screen.getByTestId("am-form-text").textContent).toBe("กำลังอ่านแบบฟอร์ม…");
    expect(screen.getByTestId("am-form").textContent).toContain("ใช้เฉพาะรอบนี้");
    expect(screen.queryByTestId("am-form-map")).toBeNull();
    expect(screen.queryByTestId("am-form-download")).toBeNull();
  });

  it("filled: the file, checked again", () => {
    render(<FormCard p={form("filled", { artifact_id: "a-1" })} />);
    expect(screen.getByTestId("am-form-text").textContent).toContain("กรอกแบบฟอร์มให้แล้ว");
    expect(screen.getByTestId("am-form-download").textContent).toBe("ดาวน์โหลดแบบฟอร์มที่กรอกแล้ว");
  });

  it("a partly filled form is a draft, never a pass: every slot it lacks is named", () => {
    render(<FormCard p={form("unfilled", { artifact_id: "a-1", unfilled: ["ยอดรวมทั้งสิ้น", "ผู้อนุมัติ"] })} />);
    expect(screen.getByTestId("am-form-text").textContent).toContain("กรอกได้ไม่ครบ 2 ช่อง");
    expect(screen.getByTestId("am-form-text").textContent).toContain("ยังไม่ควรส่งต่อ");
    expect(within(screen.getByTestId("am-form-unfilled")).getAllByRole("listitem").map((l) => l.textContent)).toEqual(["ยอดรวมทั้งสิ้น", "ผู้อนุมัติ"]);
    expect(screen.getByTestId("am-form-download").textContent).toBe("ดาวน์โหลดฉบับร่าง");
    expect(screen.getByTestId("am-form").textContent).not.toContain("กรอกแบบฟอร์มให้แล้ว");
  });

  it("needs mapping: the install's mapping screen, saved to the round", async () => {
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/runs/r-1/template" && !init?.method)
        return json({ form: { file_id: "f-9", name: "ฟอร์มสรุป.docx", kind: "docx", status: "needs_mapping", slots, mapping: null }, spec, inspect: { slots, proposal, proposed_by: "qwen3.8-27b-fp8" } });
      if (path === "/agent-market/runs/r-1/template" && init?.method === "PUT") return json({ form: { file_id: "f-9", name: "ฟอร์มสรุป.docx", kind: "docx", status: "ready" }, job: { id: "j-1" } });
      return json({}, 404);
    });
    render(<FormCard p={form("needs_mapping", { open: 2 })} />);
    expect(screen.getByTestId("am-form-text").textContent).toContain("ยังมี 2 ช่อง");
    fireEvent.click(screen.getByTestId("am-form-map"));
    const mapper = await screen.findByTestId("am-template-mapper");
    expect(within(mapper).getByTestId("am-template-proposed-by").textContent).toContain("qwen3.8-27b-fp8");
    fireEvent.click(within(mapper).getByTestId("am-template-save"));
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/runs/r-1/template" && (c[1] as RequestInit | undefined)?.method === "PUT")).toBe(true));
    const put = raw.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PUT")!;
    const body = JSON.parse(String((put[1] as RequestInit).body));
    expect(Object.keys(body).sort()).toEqual(["mapping", "slots"]);
    expect(Object.keys(body.mapping).length).toBeGreaterThan(0);
    // nothing was saved on the install
    expect(raw.mock.calls.some((c) => String(c[0]).includes("/installs/"))).toBe(false);
    await waitFor(() => expect(screen.queryByTestId("am-template-mapper")).toBeNull());
  });

  it("what the person mapped before is what the screen starts from", () => {
    const d = { form: { file_id: "f", name: "x", kind: "docx", status: "ready", slots, mapping: { [slots[0]!.key]: "company" } }, spec, inspect: { slots, proposal: { [slots[0]!.key]: "other" } } };
    expect(inspectOf(d).proposal[slots[0]!.key]).toBe("company");
    expect(inspectOf(d).diff.first).toBe(true);
  });
});
