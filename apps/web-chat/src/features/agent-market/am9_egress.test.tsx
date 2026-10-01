// AM9 (D) — where a person picks the agent's model, both levels of document
// egress are shown: the organisation's switch (the ceiling) and the agent's
// own setting under it, off by default for an agent that reads accounting.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import type { AgentEgress } from "./types";
import { EgressLine } from "./workspace/ModelControl";

const base: AgentEgress = { org: true, agent: false, agent_default: true, accounting: true, effective: false, words: "" };

afterEach(cleanup);

describe("both levels where the model is picked", () => {
  it("an accounting agent nobody has set: the organisation allows it, the agent's default does not", () => {
    render(<EgressLine egress={base} />);
    const line = screen.getByTestId("am-model-egress").textContent ?? "";
    expect(line).toContain("องค์กร: อนุญาต");
    expect(line).toContain("agent นี้: ไม่อนุญาต (ค่าตั้งต้นของ agent ที่อ่านข้อมูลบัญชี)");
  });

  it("an agent an admin opened under an organisation that is closed: the ceiling is said", () => {
    render(<EgressLine egress={{ ...base, org: false, agent: true, agent_default: false }} />);
    const line = screen.getByTestId("am-model-egress").textContent ?? "";
    expect(line).toContain("องค์กร: ไม่อนุญาต");
    expect(line).toContain("agent นี้: อนุญาต");
    expect(line).toContain("องค์กรไม่อนุญาต จึงไม่ส่ง");
  });

  it("both allow it", () => {
    render(<EgressLine egress={{ ...base, agent: true, agent_default: false, effective: true }} />);
    const line = screen.getByTestId("am-model-egress").textContent ?? "";
    expect(line).toContain("องค์กร: อนุญาต · agent นี้: อนุญาต");
    expect(line).not.toContain("ค่าตั้งต้น");
  });

  it("a server that does not send it shows nothing", () => {
    const { container } = render(<EgressLine />);
    expect(container.textContent).toBe("");
  });
});
