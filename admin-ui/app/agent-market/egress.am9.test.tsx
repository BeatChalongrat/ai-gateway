// AM9 (D) — each agent's own answer under the organisation's switch: an
// accounting agent is off until an admin turns it on; a change goes to
// chat-edge (which logs it before keeping it); the ceiling is said when the
// organisation does not allow what the agent would.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({ api: { egress: vi.fn(), putEgress: vi.fn() } }));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import type { AgentEgressRow } from "../../lib/agentMarketApi";

import AgentEgressCard from "./_sections/AgentEgress";

const wrap = (el: React.ReactNode) => render(<I18nProvider><ToastProvider>{el}</ToastProvider></I18nProvider>);
const row = (o: Partial<AgentEgressRow>): AgentEgressRow => ({
  slug: "sran.financial-report", name: "รายงานผลประกอบการ", org: true, agent: false, agent_default: true, accounting: true,
  effective: false, words: "", ...o,
});

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("per-agent egress", () => {
  it("an accounting agent nobody set stays inside; turning it on asks chat-edge for exactly that agent", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    api.putEgress.mockResolvedValue({ slug: "sran.financial-report", egress: row({ agent: true, agent_default: false, effective: true }) });
    wrap(<AgentEgressCard orgVersion="a" />);
    const toggle = (await screen.findByTestId("am-egress-toggle-sran.financial-report")) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(screen.getByTestId("am-egress-now-sran.financial-report").textContent).toMatch(/Stays inside|อยู่ในองค์กรเท่านั้น/);
    fireEvent.click(toggle);
    await waitFor(() => expect(api.putEgress).toHaveBeenCalledWith("sran.financial-report", true));
    await waitFor(() =>
      expect(screen.getByTestId("am-egress-now-sran.financial-report").textContent).toMatch(/May go to an outside model|ส่งไปโมเดลนอกองค์กรได้/),
    );
  });

  it("an agent allowed under an organisation that is closed says the ceiling holds", async () => {
    api.egress.mockResolvedValue({ org: false, agents: [row({ org: false, agent: true, agent_default: false })] });
    wrap(<AgentEgressCard orgVersion="b" />);
    const now = await screen.findByTestId("am-egress-now-sran.financial-report");
    expect(now.textContent).toMatch(/does not allow it|องค์กรไม่อนุญาต/);
  });

  // AM16: masking is no longer decided by this switch (egress.am16.test.tsx holds the second switch) —
  // and the sentence at the switch no longer says it is
  it("no longer says that this switch decides masking", async () => {
    api.egress.mockResolvedValue({ org: false, agents: [row({ org: false, agent: false, agent_default: true })] });
    wrap(<AgentEgressCard orgVersion="c" />);
    const said = (await screen.findByTestId("am-egress-masking")).textContent ?? "";
    expect(said).not.toMatch(/also decides masking|กำหนดการปิดบังข้อมูลด้วย/);
    expect(said).toMatch(/its own switch|สวิตช์ของตัวเอง/);
  });
});
