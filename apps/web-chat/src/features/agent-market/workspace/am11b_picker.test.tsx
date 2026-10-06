// AM11b part B — the model picker must not say one thing while the agent does another.
//
// AM6 drew a model the organisation's policy does not allow as a greyed row, said why once in the menu's
// header, counted the models that cannot do the job in a line per group, and AM9 added a line with both
// levels of document egress. A person could still see a model there, and the agent's page then explained
// in a paragraph that another one would be used.
//
// Now the menu is exactly the chat's picker — a model's name and its provider — over the models this agent
// can use. A model that cannot be used is not in the list: it is never offered and then swapped. When the
// list is shorter because this agent's data may not leave the organisation, one line says so.
import { useState } from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
import type { ModelEntry } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import type { AgentModels } from "../types";
import { ModelControl } from "./ModelControl";

const chatModel = (id: string, o: Partial<ModelEntry> = {}): ModelEntry => ({
  id,
  name: id,
  provider: "openai",
  description: "",
  context_window: 128000,
  capabilities: ["vision", "function_calling"],
  icon: "",
  category: "flagship",
  allowed: true,
  ...o,
});
const CHAT_MODELS: ModelEntry[] = [
  chatModel("qwen3.8-27b-fp8", { provider: "sranai", self_hosted: true }),
  chatModel("gpt-4o"),
  chatModel("claude-sonnet", { provider: "anthropic" }),
  chatModel("deepseek-v4-flash", { provider: "deepseek" }),
];

// an agent whose data may not leave the organisation: the server lists the one model that can be used
const closed = (o: Partial<AgentModels> = {}): AgentModels => ({
  uses: "text",
  choice: "auto",
  auto_model: "qwen3.8-27b-fp8",
  models: [{ id: "qwen3.8-27b-fp8", allowed: true }],
  blocked_reason: "",
  hidden_reason: "",
  reads: "",
  note: "",
  outside_absent: true,
  ...o,
});
const open = (o: Partial<AgentModels> = {}): AgentModels =>
  closed({ models: CHAT_MODELS.map((m) => ({ id: m.id, allowed: true })), outside_absent: false, ...o });

beforeEach(() => {
  raw.mockReset();
  raw.mockImplementation(async () => ({ ok: false, status: 404, text: async () => "{}" }));
  useI18nStore.setState({ locale: "th" });
  useModelStore.setState({ loaded: true, loading: false, loadFailed: false, models: CHAT_MODELS } as never);
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as never;
});
afterEach(cleanup);

function Mounted({ first }: { first: AgentModels }) {
  const [models, setModels] = useState<AgentModels | null>(first);
  return <ModelControl installId="i-1" models={models} onModels={setModels} />;
}
function openMenu(first: AgentModels): HTMLElement {
  render(<Mounted first={first} />);
  fireEvent.click(screen.getByTestId("am-model-control"));
  return screen.getByTestId("am-model-menu");
}
const rowsOf = (menu: HTMLElement) =>
  within(menu)
    .getAllByRole("option")
    .map((o) => o.textContent ?? "");

describe("AM11b — a model that cannot be used for this agent is not in the list", () => {
  it("the outside models are simply absent: no greyed row, nothing to pick and have swapped", () => {
    const menu = openMenu(closed());
    const rows = rowsOf(menu);
    expect(rows.some((r) => r.includes("qwen3.8-27b-fp8"))).toBe(true);
    for (const outside of ["gpt-4o", "claude-sonnet", "deepseek-v4-flash"]) {
      expect(rows.some((r) => r.includes(outside))).toBe(false);
      expect(menu.textContent).not.toContain(outside);
    }
    // nothing in the list is there and cannot be chosen
    expect(within(menu).queryAllByRole("option").filter((o) => o.getAttribute("aria-disabled") === "true" || (o as HTMLButtonElement).disabled)).toEqual([]);
  });

  it("one line says why the list is shorter — and nothing else is added to the chat's picker", () => {
    const menu = openMenu(closed());
    const line = within(menu).getByTestId("am-model-outside-absent");
    expect(line.textContent).toBe("โมเดลนอกองค์กรไม่อยู่ในรายการ — agent นี้ยังไม่ได้รับอนุญาตให้ส่งข้อมูลออกนอกองค์กร");
    // AM9's line of both levels of egress, AM6's "hidden: n" line and its "greyed models cannot be chosen" are gone
    expect(within(menu).queryByTestId("am-model-egress")).toBeNull();
    expect(menu.textContent).not.toMatch(/ซ่อน \d+ ตัว|สีจาง|องค์กร: (อนุญาต|ไม่อนุญาต)/);
  });

  it("when every model can be used, every one is a row and nothing is said", () => {
    const menu = openMenu(open());
    const rows = rowsOf(menu);
    for (const m of CHAT_MODELS) expect(rows.some((r) => r.includes(m.id))).toBe(true);
    expect(within(menu).queryByTestId("am-model-outside-absent")).toBeNull();
  });

  it("the pill shows what will run: a choice the server no longer lists is not shown as chosen", () => {
    // the server answers a stale choice as อัตโนมัติ (models.go: MenuFor); the pill follows it
    render(<Mounted first={closed({ choice: "auto" })} />);
    const pill = screen.getByTestId("am-model-control");
    expect(pill.textContent).toContain("อัตโนมัติ");
    expect(pill.textContent).not.toContain("gpt-4o");
  });

  it("in English the one line is English", () => {
    useI18nStore.setState({ locale: "en" });
    const menu = openMenu(closed());
    expect(within(menu).getByTestId("am-model-outside-absent").textContent).toBe(
      "Models outside the organisation are not listed — this agent may not send data outside the organisation.",
    );
  });
});
