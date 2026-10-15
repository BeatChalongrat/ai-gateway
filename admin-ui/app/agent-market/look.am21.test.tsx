// AM21 — the admin picks the organisation's look: the theme every agent's documents take, and the
// brand on them. The section shows the themes the server lists with their colours, the name that goes
// on every document, saves through the audited endpoint, and shows the server's refusal as it is said.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api, nav } = vi.hoisted(() => ({
  api: {
    me: vi.fn(),
    look: vi.fn(),
    putLook: vi.fn(),
    org: vi.fn(),
    catalog: vi.fn(),
    problems: vi.fn(),
    runs: vi.fn(),
    mail: vi.fn(),
  },
  nav: { search: "", replace: vi.fn(), push: vi.fn() },
}));

vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: nav.push, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => "/agent-market",
}));

import { ChatEdgeApiError } from "../../lib/chatEdgeApi";
import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";

import AgentMarketPage from "./page";

const PRESETS = [
  { id: "navy", name: "กรมท่า", primary: "12264A", accent: "2F6FD6", surface: "F3F5F9" },
  { id: "teal", name: "เขียวหยก", primary: "0F4C4C", accent: "1C8C84", surface: "F2F7F6" },
  { id: "charcoal", name: "ถ่านหิน", primary: "24272E", accent: "D9642B", surface: "F4F4F5" },
];
const LOOK = { theme: { id: "navy", company: "บริษัท ทดสอบ จำกัด" }, version: 2, logo: false, presets: PRESETS, file: "backend/agent_market/deliverables/look/look.json" };
const ME = { email: "boss@example.invalid", name: "Boss", admin_role: "admin", role: "admin", org_id: "org-1", sections: ["look"], can_disable: true };

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <AgentMarketPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = "section=look";
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
  api.me.mockResolvedValue(ME);
  api.look.mockResolvedValue(LOOK);
});
afterEach(cleanup);

describe("AM21 — the organisation's look", () => {
  it("shows the themes the server lists, the one in force picked, and the file the look lives in", async () => {
    renderPage();
    await screen.findByTestId("am-look");
    expect((screen.getByTestId("am-look-navy") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("am-look-teal") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("am-look-company") as HTMLInputElement).value).toBe("บริษัท ทดสอบ จำกัด");
    expect(screen.getByText("backend/agent_market/deliverables/look/look.json")).toBeTruthy();
    expect(screen.getByText("เขียวหยก")).toBeTruthy();
  });

  it("saves the picked theme, the name and a brand colour through the audited endpoint", async () => {
    api.putLook.mockResolvedValue({ theme: { id: "teal", company: "บริษัท ใหม่ จำกัด", palette: { primary: "1B3A5C" } }, version: 3, presets: PRESETS });
    renderPage();
    await screen.findByTestId("am-look");
    fireEvent.click(screen.getByTestId("am-look-teal"));
    fireEvent.change(screen.getByTestId("am-look-company"), { target: { value: "บริษัท ใหม่ จำกัด" } });
    fireEvent.change(screen.getByTestId("am-look-primary"), { target: { value: "1B3A5C" } });
    fireEvent.click(screen.getByTestId("am-look-save"));
    await waitFor(() => expect(api.putLook).toHaveBeenCalledWith({ id: "teal", company: "บริษัท ใหม่ จำกัด", primary: "1B3A5C" }));
    await screen.findByTestId("am-look-note");
    expect(screen.getByTestId("am-look-note")).toHaveTextContent("3");
  });

  it("shows the server's refusal as it is said — a marker in the name is not saved", async () => {
    api.putLook.mockRejectedValue(new ChatEdgeApiError(400, { error: { code: "BAD_VALUE", message: "ชื่อองค์กรบนเอกสารต้องไม่มีรหัสงานหรือรหัสรุ่น เช่น [AM1b] — แก้ค่านี้แล้วบันทึกอีกครั้ง ค่าเดิมยังไม่ถูกเปลี่ยน" } }));
    renderPage();
    await screen.findByTestId("am-look");
    fireEvent.change(screen.getByTestId("am-look-company"), { target: { value: "[AM1b] บริษัท ตัวอย่าง จำกัด" } });
    fireEvent.click(screen.getByTestId("am-look-save"));
    await screen.findByTestId("am-look-note");
    expect(screen.getByTestId("am-look-note")).toHaveTextContent("[AM1b]");
  });
});
