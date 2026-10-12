// AM16 — to whom. "Documents may leave" said yes or no, not who receives. The
// admin names the recipients: each a route AND the company that runs the model
// behind it (both read what is sent); the organisation's list is the ceiling,
// an agent's ticks can only be among it; and an agent whose documents may leave
// with nobody ticked reaches nobody — which the row must say, not leave to be
// found out.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: { egress: vi.fn(), putEgress: vi.fn(), putMasked: vi.fn(), putOrgRecipients: vi.fn(), putRecipients: vi.fn() },
}));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import type { AgentEgressRow, RecipientOption } from "../../lib/agentMarketApi";

import AgentEgressCard from "./_sections/AgentEgress";

const SLUG = "sran.invoice-processing";
const wrap = (el: React.ReactNode) => render(<I18nProvider><ToastProvider>{el}</ToastProvider></I18nProvider>);
const row = (o: Partial<AgentEgressRow>): AgentEgressRow => ({
  slug: SLUG, name: "ทะเบียนใบกำกับภาษีซื้อ", org: true, agent: true, agent_default: false, accounting: true,
  effective: true, words: "", masked: false, pages_may_leave: true, recipients: [], agent_recipients: [], org_recipients: [], ...o,
});
const AVAILABLE: RecipientOption[] = [
  { key: "deepseek", route: "deepseek", company: "deepseek", models: ["deepseek-v4-flash"] },
  { key: "openrouter:amazon", route: "openrouter", company: "amazon", models: ["anthropic/claude-sonnet-4.5"] },
  { key: "openrouter:google", route: "openrouter", company: "google", models: ["anthropic/claude-sonnet-4.5", "google/gemini-2.5-flash"] },
];
const now = () => screen.getByTestId(`am-egress-now-${SLUG}`);
const orgBox = (key: string) => screen.getByTestId(`am-recipient-org-${key}`) as HTMLInputElement;
const agentBox = (key: string) => screen.queryByTestId(`am-recipient-${SLUG}-${key}`) as HTMLInputElement | null;

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("who an agent's documents may reach", () => {
  it("may leave with nobody named: the row says it reaches nobody", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})], org_recipients: [], available: AVAILABLE });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    expect(now().getAttribute("data-leaves")).toBe("yes");
    expect(now().getAttribute("data-reaches")).toBe("");
    expect(screen.getByTestId(`am-reaches-now-${SLUG}`).textContent).toMatch(/nobody named — reaches nobody outside|ยังไม่ได้เลือกผู้รับ — ไม่ส่งถึงใครนอกองค์กร/);
    // and the agent has nothing to tick: the organisation has named nobody
    expect(screen.getByTestId(`am-recipients-${SLUG}`).textContent).toMatch(/The organisation has named nobody yet|องค์กรยังไม่ได้เลือกผู้รับ/);
    expect(agentBox("openrouter:google")).toBeNull();
  });

  it("every recipient is offered with both its names, and the models it runs", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})], org_recipients: [], available: AVAILABLE });
    wrap(<AgentEgressCard orgVersion="a" />);
    const list = await screen.findByTestId("am-recipients-org");
    const said = list.textContent ?? "";
    // the company that runs the model AND the route — never one name alone for a routed recipient
    expect(said).toMatch(/Google (via|ผ่าน) OpenRouter/);
    expect(said).toMatch(/Amazon Bedrock (via|ผ่าน) OpenRouter/);
    expect(said).toMatch(/DeepSeek \((direct|ตรง)\)/);
    expect(said).toContain("anthropic/claude-sonnet-4.5, google/gemini-2.5-flash");
    expect(orgBox("openrouter:google").checked).toBe(false);
    expect(orgBox("openrouter:amazon").checked).toBe(false);
  });

  it("naming one for the organisation sends the organisation's whole list — and no agent's switch", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})], org_recipients: [], available: AVAILABLE });
    api.putOrgRecipients.mockResolvedValue({ org_recipients: ["openrouter:google"] });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    api.egress.mockResolvedValue({ org: true, agents: [row({ org_recipients: ["openrouter:google"] })], org_recipients: ["openrouter:google"], available: AVAILABLE });
    fireEvent.click(orgBox("openrouter:google"));
    await waitFor(() => expect(api.putOrgRecipients).toHaveBeenCalledWith(["openrouter:google"]));
    expect(api.putRecipients).not.toHaveBeenCalled();
    expect(api.putEgress).not.toHaveBeenCalled();
    await waitFor(() => expect(orgBox("openrouter:google").checked).toBe(true));
    // the agent can now tick it — and until it does, it still reaches nobody
    await waitFor(() => expect(agentBox("openrouter:google")).not.toBeNull());
    expect(agentBox("openrouter:google")!.checked).toBe(false);
    expect(agentBox("openrouter:amazon")).toBeNull(); // not on the organisation's list: not offered to an agent
    expect(now().getAttribute("data-reaches")).toBe("");
  });

  it("an agent's tick sends that agent's list, and the row then says whom it reaches — both names", async () => {
    const org = ["deepseek", "openrouter:google"];
    api.egress.mockResolvedValue({ org: true, agents: [row({ org_recipients: org })], org_recipients: org, available: AVAILABLE });
    api.putRecipients.mockResolvedValue({ slug: SLUG, egress: row({ org_recipients: org, agent_recipients: ["openrouter:google"], recipients: ["openrouter:google"] }) });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    fireEvent.click(agentBox("openrouter:google")!);
    await waitFor(() => expect(api.putRecipients).toHaveBeenCalledWith(SLUG, ["openrouter:google"]));
    expect(api.putOrgRecipients).not.toHaveBeenCalled();
    await waitFor(() => expect(now().getAttribute("data-reaches")).toBe("openrouter:google"));
    expect(screen.getByTestId(`am-reaches-now-${SLUG}`).textContent).toMatch(/reaches Google via OpenRouter|ส่งถึง Google ผ่าน OpenRouter/);
    expect(agentBox("deepseek")!.checked).toBe(false);
  });

  it("a tick the organisation has since taken off its list is shown as not counting, and the row says nobody", async () => {
    api.egress.mockResolvedValue({
      org: true,
      agents: [row({ org_recipients: ["deepseek"], agent_recipients: ["openrouter:google"], recipients: [] })],
      org_recipients: ["deepseek"],
      available: AVAILABLE,
    });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    expect(agentBox("openrouter:google")!.checked).toBe(true);
    expect(screen.getByTestId(`am-recipients-${SLUG}`).textContent).toMatch(/does not count|ไม่นับ/);
    expect(screen.getByTestId(`am-reaches-now-${SLUG}`).textContent).toMatch(/reaches nobody outside|ไม่ส่งถึงใครนอกองค์กร/);
  });

  it("an agent whose documents stay inside says nothing about recipients in its row", async () => {
    const org = ["openrouter:google"];
    api.egress.mockResolvedValue({
      org: true,
      agents: [row({ agent: false, effective: false, pages_may_leave: false, org_recipients: org, agent_recipients: org, recipients: [] })],
      org_recipients: org,
      available: AVAILABLE,
    });
    wrap(<AgentEgressCard orgVersion="a" />);
    await screen.findByTestId(`am-egress-${SLUG}`);
    expect(screen.queryByTestId(`am-reaches-now-${SLUG}`)).toBeNull();
    expect(now().getAttribute("data-leaves")).toBe("no");
  });

  it("with no provider in the catalogue there is nobody to tick, and it says so", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})], org_recipients: [], available: [] });
    wrap(<AgentEgressCard orgVersion="a" />);
    const list = await screen.findByTestId("am-recipients-org");
    expect(list.textContent).toMatch(/nobody to tick|ยังไม่มีใครให้เลือก/);
  });
});
