// AM16 — masking is the admin's own switch per agent, beside "documents may
// leave": two separate switches, both off by default. Turning one does not turn
// the other; each asks chat-edge for exactly that (which writes it to the audit
// log before keeping it). The row says in words what the two together mean now
// — including the one an admin must not miss: may leave AND not masked.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({ api: { egress: vi.fn(), putEgress: vi.fn(), putMasked: vi.fn() } }));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import type { AgentEgressRow } from "../../lib/agentMarketApi";

import AgentEgressCard from "./_sections/AgentEgress";

const SLUG = "sran.invoice-processing";
const wrap = (el: React.ReactNode) => render(<I18nProvider><ToastProvider>{el}</ToastProvider></I18nProvider>);
const row = (o: Partial<AgentEgressRow>): AgentEgressRow => ({
  slug: SLUG, name: "ทะเบียนใบกำกับภาษีซื้อ", org: true, agent: false, agent_default: true, accounting: true,
  effective: false, words: "", masked: false, pages_may_leave: false, ...o,
});
const leave = () => screen.getByTestId(`am-egress-toggle-${SLUG}`) as HTMLInputElement;
const masked = () => screen.getByTestId(`am-masked-toggle-${SLUG}`) as HTMLInputElement;
const now = () => screen.getByTestId(`am-egress-now-${SLUG}`);

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("an agent's two switches", () => {
  it("both are off for an agent nobody has set, and the row says what that means", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    expect(leave().checked).toBe(false);
    expect(masked().checked).toBe(false);
    expect(now().getAttribute("data-leaves")).toBe("no");
    expect(now().getAttribute("data-masked")).toBe("no");
    expect(now().textContent).toMatch(/Stays inside · sent to the in-house model as typed|อยู่ในองค์กรเท่านั้น · ส่งให้โมเดลในองค์กรตามที่พิมพ์/);
    // each says it is at its default
    expect(screen.getByTestId(`am-egress-${SLUG}`).textContent?.match(/default: off|ค่าตั้งต้น: ปิด/g)?.length).toBe(2);
  });

  it("turning masking on asks for masking only — the leave switch is not sent, and does not move", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    api.putMasked.mockResolvedValue({ slug: SLUG, egress: row({ masked: true }) });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    fireEvent.click(masked());
    await waitFor(() => expect(api.putMasked).toHaveBeenCalledWith(SLUG, true));
    expect(api.putEgress).not.toHaveBeenCalled();
    await waitFor(() => expect(masked().checked).toBe(true));
    expect(leave().checked).toBe(false);
    expect(now().textContent).toMatch(/Stays inside · personal data masked before a model sees it|อยู่ในองค์กรเท่านั้น · ปิดบังข้อมูลส่วนบุคคลก่อนถึงโมเดล/);
  });

  it("letting documents leave asks for that only — masking stays as it was", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({ masked: true })] });
    api.putEgress.mockResolvedValue({ slug: SLUG, egress: row({ agent: true, agent_default: false, effective: true, masked: true }) });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    fireEvent.click(leave());
    await waitFor(() => expect(api.putEgress).toHaveBeenCalledWith(SLUG, true));
    expect(api.putMasked).not.toHaveBeenCalled();
    await waitFor(() => expect(leave().checked).toBe(true));
    expect(masked().checked).toBe(true);
    // may leave and masked: text goes out masked, a page stays inside
    expect(now().textContent).toMatch(/May go to an outside model · text masked first; pages read inside only|ส่งไปโมเดลนอกองค์กรได้ · ปิดบังข้อความก่อนส่ง ส่วนหน้าเอกสารอ่านในองค์กรเท่านั้น/);
  });

  it("may leave and not masked is said in so many words", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({ agent: true, agent_default: false, effective: true, masked: false, pages_may_leave: true })] });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    expect(now().getAttribute("data-leaves")).toBe("yes");
    expect(now().getAttribute("data-masked")).toBe("no");
    expect(now().textContent).toMatch(/May go to an outside model · sent as typed, not masked|ส่งไปโมเดลนอกองค์กรได้ · ส่งตามที่พิมพ์ ไม่ปิดบัง/);
  });

  it("says, where both are decided, what each switch does — and that both are logged and a run says so", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    wrap(<AgentEgressCard orgVersion="a" />);
    const said = (await screen.findByTestId("am-egress-masking")).textContent ?? "";
    expect(said).toMatch(/its own switch|สวิตช์ของตัวเอง/);
    expect(said).toMatch(/off unless you turn it on|ปิดอยู่จนกว่าคุณจะเปิด/);
    expect(said).toMatch(/sent as it was typed|ส่งไปตามที่พิมพ์/);
    expect(said).toMatch(/cannot be masked|ปิดบังไม่ได้/);
    expect(said).toMatch(/audit log|บันทึกการตรวจสอบ/);
    expect(said).toMatch(/says so on its card|บอกไว้บนการ์ดของรอบนั้น/);
    // the old rule is not said any more
    expect(said).not.toMatch(/also decides masking|กำหนดการปิดบังข้อมูลด้วย/);
  });

  it("a refused change says so and moves nothing", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    api.putMasked.mockRejectedValue(new Error("AUDIT_UNAVAILABLE"));
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    fireEvent.click(masked());
    await waitFor(() => expect(api.putMasked).toHaveBeenCalled());
    await waitFor(() => expect(masked().disabled).toBe(false));
    expect(masked().checked).toBe(false);
  });
});
