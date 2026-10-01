// AM9 (I) — "send as me": the draft says which mailbox an approved message
// leaves from (the person's own, or the organisation's relay on their
// behalf), the confirm dialog says it again, and a person whose deployment can
// connect a mailbox is offered to connect theirs — through the backend's
// connect ticket, asking to send only. With nothing configured, nothing new.
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
import { GATEWAY_URL } from "@/lib/auth";

import type { EmailDraft } from "../types";
import { EmailDraftCard } from "./EmailDraftCard";

const draft = (o: Partial<EmailDraft> = {}): EmailDraft => ({
  id: "d-1",
  run_id: "r-1",
  subject: "งบกระทบยอดเงินฝากธนาคาร กันยายน 2569",
  body: "เรียน ผู้จัดการฝ่ายบัญชี\n\nเสร็จแล้ว",
  attachments: [{ artifact_id: "a1", name: "bank_2569-09.xlsx", size: 20480, format: "xlsx" }],
  can_send: true,
  body_sha256: "9f2c",
  created_at: "2026-09-29T03:10:00Z",
  sent: [],
  ...o,
});
const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

beforeEach(() => {
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("which mailbox sends", () => {
  it("a connected mailbox: the card and the confirm dialog say it leaves from the person's own address", () => {
    render(<EmailDraftCard draft={draft({ transport: "mailbox", mailbox: { available: true, connected: true } })} />);
    const line = screen.getByTestId("am-email-transport");
    expect(line.getAttribute("data-transport")).toBe("mailbox");
    expect(line.textContent).toContain("ส่งจากอีเมลของคุณที่เชื่อมต่อไว้");
    expect(screen.queryByTestId("am-email-connect")).toBeNull();
    fireEvent.change(screen.getByLabelText("ถึง"), { target: { value: "cfo@company.example" } });
    fireEvent.click(screen.getByRole("button", { name: /ส่ง/ }));
    expect(screen.getByTestId("am-email-confirm-via").textContent).toContain("ส่งจากอีเมลของคุณ");
  });

  it("the relay sends on the person's behalf, and their own mailbox can be connected — asking to send only", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ ticket: "t", start_url: "https://backend.example/portal/connectors/oauth/start?ticket=t" }) }));
    vi.stubGlobal("fetch", fetchMock);
    raw.mockImplementation(async (path: string) =>
      path === "/agent-market/email-drafts/d-1" ? json({ draft: draft({ transport: "mailbox", mailbox: { available: true, connected: true } }) }) : json({}, 404),
    );
    render(<EmailDraftCard draft={draft({ transport: "relay", mailbox: { available: true, connected: false } })} />);
    expect(screen.getByTestId("am-email-transport").textContent).toContain("ส่งผ่านเซิร์ฟเวอร์อีเมลขององค์กรในนามของคุณ");
    fireEvent.click(screen.getByTestId("am-email-connect"));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://backend.example/portal/connectors/oauth/start?ticket=t", "_blank", "noopener,noreferrer"));
    expect(fetchMock).toHaveBeenCalledWith(`${GATEWAY_URL}/portal/connectors/mail/ticket`, { method: "POST", credentials: "include" });
    expect(screen.getByText(/ขอเฉพาะสิทธิ์ส่งอีเมล ไม่ขอสิทธิ์อ่าน/)).toBeTruthy();
    // connected in the other tab: checking again turns the card to the person's own mailbox
    fireEvent.click(screen.getByTestId("am-email-connect-check"));
    await waitFor(() => expect(screen.getByTestId("am-email-transport").getAttribute("data-transport")).toBe("mailbox"));
  });

  it("nothing configured: no line, no connect — the draft is copied as before", () => {
    render(<EmailDraftCard draft={draft({ can_send: false, cannot_send_reason: "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล", transport: "", mailbox: { available: false, connected: false } })} />);
    expect(screen.queryByTestId("am-email-transport")).toBeNull();
    expect(screen.getByTestId("am-email-cannot-send").textContent).toContain("ยังไม่ได้ตั้งค่า");
  });

  it("a start address that is not http(s) is never opened", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ start_url: "javascript:alert(1)" }) })));
    render(<EmailDraftCard draft={draft({ transport: "relay", mailbox: { available: true, connected: false } })} />);
    fireEvent.click(screen.getByTestId("am-email-connect"));
    await waitFor(() => expect(screen.getByText("เปิดหน้าเชื่อมต่ออีเมลไม่ได้ ลองอีกครั้ง")).toBeTruthy());
    expect(open).not.toHaveBeenCalled();
  });
});
