// AM6 part D — the agent's model menu is the chat's model menu, with no
// additions of any kind.
//
// AM4 and AM5 gave the agent a menu of its own: rows named by outcome
// ("เร็วและประหยัด", "แม่นที่สุด"), a cost and a time on every row, "ในองค์กร",
// the reason a row was locked on the row, a sentence about the single
// in-house model, "เลือกโมเดลเอง" to reach the real list, "ใช้กับรอบนี้" and
// "ใช้เป็นค่าตั้งต้น". The owner looked at both screens and cancelled all of it.
//
// What stays is not on the rows: a model the organisation does not allow
// cannot be chosen, and its reason is said once; a model that cannot do the
// agent's job is counted in one line at the end of its group.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { useState } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import { ModelSelector } from "@/components/chat/ModelSelector";
import { useToastStore } from "@/components/ui/Toast";
import { useI18nStore } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import { amDictionaries } from "../i18n";
import { ModelSection } from "../settings/ModelSection";
import type { AgentModels } from "../types";
import { ModelControl } from "./ModelControl";

const BLOCKED = "องค์กรไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร โมเดลนอกองค์กรจึงเลือกไม่ได้ — ผู้ดูแลเปิดได้ในหน้าผู้ดูแล";
const WHY = "agent นี้ต้องใช้โมเดลที่อ่านภาพได้";
const READS = "โมเดลอ่านเฉพาะเอกสารที่เป็นสแกนหรือรูปถ่าย ตัวเลขทุกตัวคำนวณด้วยสคริปต์";

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
const TEXT_ONLY = { capabilities: ["function_calling"] };
const OWN = { provider: "sranai", self_hosted: true };

// what this person's chat lists: seven in-house models (one reads images), four outside, one that makes video
const CHAT_MODELS: ModelEntry[] = [
  chatModel("qwen3.8-27b-fp8", OWN),
  chatModel("typhoon-text", { ...OWN, ...TEXT_ONLY }),
  chatModel("pathumma-text", { ...OWN, ...TEXT_ONLY }),
  chatModel("openthaigpt", { ...OWN, ...TEXT_ONLY }),
  chatModel("wangchan", { ...OWN, ...TEXT_ONLY }),
  chatModel("seallm", { ...OWN, ...TEXT_ONLY }),
  chatModel("scb-text", { ...OWN, ...TEXT_ONLY }),
  chatModel("gpt-4o"),
  chatModel("gpt-4o-mini"),
  chatModel("claude-sonnet", { provider: "anthropic" }),
  chatModel("deepseek-v4-flash", { provider: "deepseek", ...TEXT_ONLY }),
  chatModel("sora-video", { model_kind: "video" }),
];

// what the server says of an agent that reads images, in an organisation that keeps its documents inside
const agentModels = (o: Partial<AgentModels> = {}): AgentModels => ({
  uses: "vision",
  choice: "auto",
  auto_model: "qwen3.8-27b-fp8",
  models: [
    { id: "qwen3.8-27b-fp8", allowed: true },
    { id: "gpt-4o", allowed: false },
    { id: "gpt-4o-mini", allowed: false },
    { id: "claude-sonnet", allowed: false },
  ],
  blocked_reason: BLOCKED,
  hidden_reason: WHY,
  reads: READS,
  note: "",
  ...o,
});

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const calls = (path: string, method?: string) => raw.mock.calls.filter((c) => c[0] === path && (!method || (c[1] as RequestInit | undefined)?.method === method));

beforeEach(() => {
  raw.mockReset();
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => handler(path, init));
  useI18nStore.setState({ locale: "th" }); // AM11: the workspace follows the app's language; these tests read it in Thai
  useModelStore.setState({ loaded: true, loading: false, loadFailed: false, models: CHAT_MODELS } as never);
  useToastStore.getState().clear();
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
  window.localStorage.clear();
});
afterEach(cleanup);

/** The control as the workspace mounts it: what the server answers a PUT with becomes what it shows. */
function Mounted({ first }: { first: AgentModels }) {
  const [models, setModels] = useState<AgentModels | null>(first);
  return <ModelControl installId="i-1" models={models} onModels={setModels} />;
}

let page: HTMLElement;

function openMenu(first: AgentModels = agentModels()): HTMLElement {
  page = render(<Mounted first={first} />).container;
  fireEvent.click(screen.getByTestId("am-model-control"));
  return screen.getByTestId("am-model-menu");
}

/** The chat's own model menu for the same person — in the app's language, as the workspace reads too (AM11). */
function chatMenu<T>(read: (menu: HTMLElement) => T, props: { needsVision?: boolean } = {}): T {
  const chat = render(<ModelSelector activeModelId="auto" compact {...props} />);
  fireEvent.click(chat.getByTestId("model-trigger-label"));
  const got = read(chat.getByRole("listbox"));
  chat.unmount();
  return got;
}

interface Group {
  provider: string;
  count: string;
  rows: string[];
  hidden: string | null;
}

/** The provider groups of a model menu as a person reads them, every "…more" opened. */
function groups(menu: HTMLElement): Group[] {
  for (;;) {
    const more = within(menu)
      .queryAllByRole("button")
      .find((b) => /^…(อีก \d+ รายการ|\d+ more)$/.test(b.textContent ?? ""));
    if (!more) break;
    fireEvent.click(more);
  }
  const out: Group[] = [];
  for (const key of ["full", "own"]) {
    const block = menu.querySelector(`[data-testid="model-group-${key}"]`);
    if (!block) continue;
    for (const section of Array.from(block.children).slice(1)) {
      const header = section.firstElementChild as HTMLElement;
      out.push({
        provider: header.querySelector("span.uppercase")?.textContent ?? "",
        count: header.lastElementChild?.textContent ?? "",
        rows: Array.from(section.querySelectorAll('[role="option"] [data-testid="model-row-name"]')).map((n) => n.textContent ?? ""),
        hidden: section.querySelector('[data-testid="model-group-hidden"]')?.textContent ?? null,
      });
    }
  }
  return out;
}

const hiddenCount = (line: string | null) => Number(/ซ่อน (\d+) ตัว/.exec(line ?? "")?.[1] ?? 0);
const rowOf = (menu: HTMLElement, name: string) => within(menu).getByText(name).closest('[role="option"]') as HTMLElement;

describe("AM6 — the agent's model menu is the chat's model menu", () => {
  it("opens on the chat's picker: the search box, the provider groups, อัตโนมัติ as a row of the list", () => {
    // (an agent every model may be used for: AM11b lists no other, so a closed agent has no outside group)
    const menu = openMenu(agentModels({ models: (agentModels().models ?? []).map((m) => ({ ...m, allowed: true })), blocked_reason: "" }));
    expect(within(menu).getByPlaceholderText("ค้นหาโมเดล…")).toBeTruthy();
    expect(within(menu).getByTestId("model-group-own")).toBeTruthy();
    expect(within(menu).getByTestId("model-group-full")).toBeTruthy();
    const rows = within(menu).getAllByRole("option");
    expect(within(rows[0]!).getByTestId("model-row-name").textContent).toBe("อัตโนมัติ");
    expect(rows[0]!.getAttribute("aria-selected")).toBe("true");
    // an agent reads documents: no video, audio or voice models
    expect(within(menu).queryByText("sora-video")).toBeNull();
  });

  it("has nothing of its own in the menu: no tiers, no cost or time, no tag, no second list, no per-round choice", () => {
    const menu = openMenu();
    for (const id of ["am-model-option", "am-model-custom", "am-model-custom-row", "am-model-back", "am-model-single"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    expect(screen.queryByTestId("am-model-save-default")).toBeNull();
    const text = menu.textContent ?? "";
    for (const word of ["เลือกโมเดลเอง", "แนะนำ", "เร็วและประหยัด", "แม่นที่สุด", "บาท", "วินาที", "ในองค์กร", "ค่าตั้งต้น", "ใช้กับรอบนี้", "ตัวเดียว"]) {
      expect(text).not.toContain(word);
    }
    // what the chat's picker is made of, and nothing beside it: the picker is the menu's only content
    // (below 640 px the shell adds its own drag handle, as in the chat)
    const content = Array.from(menu.children).filter((c) => !c.hasAttribute("aria-hidden"));
    expect(content).toHaveLength(1);
    expect(content[0]!.querySelector("input")).toBeTruthy();
  });

  it("a row is the chat's row for that model: what the chat writes, nothing added", () => {
    const want = chatMenu((menu) => {
      groups(menu);
      return ["อัตโนมัติ", "qwen3.8-27b-fp8"].map((n) => [n, rowOf(menu, n).outerHTML] as const);
    });

    const menu = openMenu();
    groups(menu);
    for (const [name, html] of want) expect(rowOf(menu, name).outerHTML).toBe(html);
  });

  it("the search finds a model by name", () => {
    const menu = openMenu();
    fireEvent.change(within(menu).getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "qwen" } });
    expect(within(menu).getAllByTestId("model-row-name").map((n) => n.textContent)).toEqual(["qwen3.8-27b-fp8"]);
    // AM11b: a model this agent may not use is not found either — it is not in the list
    fireEvent.change(within(menu).getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "claude" } });
    expect(within(menu).queryAllByTestId("model-row-name")).toEqual([]);
  });

  it("an agent that sends nothing to a model has no model control", () => {
    const { container } = render(<Mounted first={agentModels({ uses: "none", models: [] })} />);
    expect(container.querySelector("button")).toBeNull();
    expect(screen.queryByTestId("am-model-control")).toBeNull();
  });
});

// AM11b supersedes AM6 here. AM6 drew a model the policy does not allow as a greyed row and said why once
// in the header; and counted the models that cannot do the job in a line per group. A person could still
// see a model in the menu that the agent would not use. Now neither kind is in the list at all
// (am11b_picker.test.tsx has the one line that says why the list is shorter).
describe("AM11b — a model the organisation's policy does not allow is not in the list", () => {
  it("no greyed row, no reason in the header: the model is absent", () => {
    const menu = openMenu();
    expect(within(menu).queryByTestId("model-row-disabled")).toBeNull();
    expect(within(menu).queryByTestId("model-picker-notice")).toBeNull();
    const names = within(menu)
      .getAllByTestId("model-row-name")
      .map((n) => n.textContent);
    for (const outside of ["claude-sonnet", "gpt-4o", "gpt-4o-mini"]) expect(names).not.toContain(outside);
    expect(menu.textContent).not.toContain(BLOCKED);
  });

  it("so there is nothing to pick and have swapped: every row that is there can be chosen", async () => {
    const menu = openMenu();
    handler = (path, init) => (path === "/agent-market/installs/i-1/model" && init?.method === "PUT" ? json(agentModels({ choice: "qwen3.8-27b-fp8" })) : json({}, 404));
    fireEvent.click(rowOf(menu, "qwen3.8-27b-fp8"));
    await waitFor(() => expect(calls("/agent-market/installs/i-1/model", "PUT")).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("am-model-control").textContent).toContain("qwen3.8-27b-fp8"));
  });

  it("when the policy allows them, they are rows like any other", () => {
    const menu = openMenu(agentModels({ models: (agentModels().models ?? []).map((m) => ({ ...m, allowed: true })), blocked_reason: "" }));
    const names = within(menu)
      .getAllByTestId("model-row-name")
      .map((n) => n.textContent);
    for (const outside of ["claude-sonnet", "gpt-4o", "gpt-4o-mini"]) expect(names).toContain(outside);
    expect(within(menu).queryByTestId("model-row-disabled")).toBeNull();
  });
});

describe("AM11b — a model that cannot do the agent's job is not in the list, and nothing counts it", () => {
  it("seven in-house models, one that reads images: that one row, and no line about the six", () => {
    const menu = openMenu();
    const own = groups(menu).find((g) => g.provider === "sranai")!;
    expect(own.rows).toEqual(["qwen3.8-27b-fp8"]);
    expect(own.hidden).toBeNull();
    expect(own.count).toBe("1");
    expect(within(menu).queryByTestId("model-group-hidden")).toBeNull();
    expect(menu.textContent).not.toMatch(/ซ่อน \d+ ตัว/);
  });

  it("a provider none of whose models can be used has no header at all", () => {
    const got = groups(openMenu());
    expect(got.map((g) => g.provider)).toEqual(["sranai"]);
    expect(got.every((g) => g.rows.length > 0)).toBe(true);
  });

  it("every header counts the rows under it", () => {
    const got = groups(openMenu(agentModels({ models: (agentModels().models ?? []).map((m) => ({ ...m, allowed: true })), blocked_reason: "" })));
    expect(got.map((g) => [g.provider, g.count, g.rows.length])).toEqual([
      ["openai", "2", 2],
      ["anthropic", "1", 1],
      ["sranai", "1", 1],
    ]);
  });

  it("the same for an agent whose model writes words: every model the server names is a row", () => {
    const all = CHAT_MODELS.filter((m) => !m.model_kind).map((m) => ({ id: m.id, allowed: true }));
    const got = groups(openMenu(agentModels({ uses: "text", models: all, blocked_reason: "", hidden_reason: "" })));
    expect(got.map((g) => [g.provider, g.rows.length, g.hidden])).toEqual([
      ["openai", 2, null],
      ["anthropic", 1, null],
      ["deepseek", 1, null],
      ["sranai", 7, null],
    ]);
  });
});

describe("AM6 — picking a model sets the agent's model", () => {
  it("the PUT, then the pill; nothing is kept for one round only", async () => {
    handler = (path, init) =>
      path === "/agent-market/installs/i-1/model" && init?.method === "PUT" ? json(agentModels({ choice: "qwen3.8-27b-fp8" })) : json({}, 404);
    const menu = openMenu();
    fireEvent.click(rowOf(menu, "qwen3.8-27b-fp8"));
    await waitFor(() => expect(calls("/agent-market/installs/i-1/model", "PUT")).toHaveLength(1));
    expect(JSON.parse(String((calls("/agent-market/installs/i-1/model")[0]![1] as RequestInit).body))).toEqual({ choice: "qwen3.8-27b-fp8" });
    expect(screen.queryByTestId("am-model-menu")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("model-trigger-label").textContent).toBe("qwen3.8-27b-fp8"));
    expect(screen.queryByTestId("am-model-save-default")).toBeNull();
    // beside the pill: nothing
    expect(page.textContent).toBe("qwen3.8-27b-fp8");

    // the tick is on it the next time the menu opens
    fireEvent.click(screen.getByTestId("am-model-control"));
    const again = screen.getByTestId("am-model-menu");
    expect(rowOf(again, "qwen3.8-27b-fp8").getAttribute("aria-selected")).toBe("true");
    expect(within(again).getAllByRole("option")[0]!.getAttribute("aria-selected")).toBe("false");
  });

  it("อัตโนมัติ is picked like any other row", async () => {
    handler = () => json(agentModels({ choice: "auto" }));
    const menu = openMenu(agentModels({ choice: "qwen3.8-27b-fp8" }));
    expect(screen.getByTestId("model-trigger-label").textContent).toBe("qwen3.8-27b-fp8");
    fireEvent.click(within(menu).getAllByRole("option")[0]!);
    await waitFor(() => expect(calls("/agent-market/installs/i-1/model", "PUT")).toHaveLength(1));
    expect(JSON.parse(String((calls("/agent-market/installs/i-1/model")[0]![1] as RequestInit).body))).toEqual({ choice: "auto" });
    await waitFor(() => expect(screen.getByTestId("model-trigger-label").textContent).toBe("อัตโนมัติ"));
  });

  it("the model already chosen is not sent again", () => {
    const menu = openMenu(agentModels({ choice: "qwen3.8-27b-fp8" }));
    fireEvent.click(rowOf(menu, "qwen3.8-27b-fp8"));
    expect(raw).not.toHaveBeenCalled();
    expect(screen.queryByTestId("am-model-menu")).toBeNull();
  });

  it("a choice the server refuses: the pill says what the agent still uses, and the person is told why", async () => {
    handler = () => json({ error: { code: "MODEL_NOT_ALLOWED", message: "องค์กรไม่อนุญาตให้ใช้โมเดลนี้กับ agent" } }, 403);
    const menu = openMenu();
    fireEvent.click(rowOf(menu, "qwen3.8-27b-fp8"));
    await waitFor(() => expect(useToastStore.getState().message).toBe("องค์กรไม่อนุญาตให้ใช้โมเดลนี้กับ agent"));
    expect(useToastStore.getState().kind).toBe("error");
    expect(screen.getByTestId("model-trigger-label").textContent).toBe("อัตโนมัติ");
  });
});

describe("AM6 — the settings page has the same control", () => {
  const settingsHandler = (models: AgentModels): Handler => (path, init) => {
    if (path === "/agent-market/installs/i-1/models") return json(models);
    if (path === "/agent-market/installs/i-1/model" && init?.method === "PUT") return json({ ...models, choice: JSON.parse(String(init.body)).choice });
    return json({}, 404);
  };

  it("the control and the one sentence about what the model does for this agent, nothing else", async () => {
    handler = settingsHandler(agentModels());
    const workspace = render(<Mounted first={agentModels()} />);
    const want = (workspace.getByTestId("am-model-control") as HTMLElement).outerHTML;
    workspace.unmount();

    render(<ModelSection installId="i-1" />);
    const section = await screen.findByTestId("am-settings-models");
    expect(within(section).getByTestId("am-model-control").outerHTML).toBe(want);
    expect(section.querySelector("h2")?.textContent).toBe("โมเดลที่อ่านเอกสาร");
    expect(section.textContent).toBe(`โมเดลที่อ่านเอกสาร${READS}อัตโนมัติ`);
    expect(within(section).queryByRole("radiogroup")).toBeNull();
    expect(within(section).queryByTestId("am-settings-model")).toBeNull();
  });

  it("opens the same menu and saves the same way", async () => {
    handler = settingsHandler(agentModels());
    render(<ModelSection installId="i-1" />);
    const section = await screen.findByTestId("am-settings-models");
    fireEvent.click(within(section).getByTestId("am-model-control"));
    const menu = screen.getByTestId("am-model-menu");
    expect(within(menu).queryByTestId("model-picker-notice")).toBeNull();
    fireEvent.click(rowOf(menu, "qwen3.8-27b-fp8"));
    await waitFor(() => expect(calls("/agent-market/installs/i-1/model", "PUT")).toHaveLength(1));
    await waitFor(() => expect(within(section).getByTestId("model-trigger-label").textContent).toBe("qwen3.8-27b-fp8"));
  });

  it("an agent that sends nothing to a model: the sentence, no control", async () => {
    handler = settingsHandler(agentModels({ uses: "none", models: [], reads: "agent นี้ไม่ส่งเอกสารให้โมเดลใด" }));
    render(<ModelSection installId="i-1" />);
    const section = await screen.findByTestId("am-settings-models");
    expect(section.textContent).toContain("agent นี้ไม่ส่งเอกสารให้โมเดลใด");
    expect(within(section).queryByTestId("am-model-control")).toBeNull();
  });
});

// ─────────────────────────── the sources ───────────────────────────

const FEATURE = resolve(__dirname, "..");

function sources(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sources(path, acc);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) acc.push(path);
  }
  return acc;
}

/** Every place in the feature's sources where the pattern is written, as "file: what". */
function written(pattern: RegExp): string[] {
  const found: string[] = [];
  for (const file of sources(FEATURE)) {
    const names = new Set(readFileSync(file, "utf8").match(pattern) ?? []);
    for (const name of names) found.push(`${relative(FEATURE, file).replace(/\\/g, "/")}: ${name}`);
  }
  return found.sort();
}

describe("AM6 — what was cancelled is deleted, not switched off", () => {
  it("no tiers, cost rows, single-model notice, second list or per-round choice left in the feature's code", () => {
    expect(
      written(/\b(?:ModelOption|CustomModel|interface ModelPicker|useModelPicker|collapseOptions|optionFacts|optionLine|single_in_house|locked_reason|modelChoice|roundChoice|agent_models|adminModels|AdminModels|cost_thb)\b/g),
    ).toEqual([]);
    // the install's own model is the one choice the server's rows still name
    expect(written(/\bmodel_choice\b/g)).toEqual(["types.ts: model_choice"]);
    expect(readFileSync(join(FEATURE, "types.ts"), "utf8").match(/\bmodel_choice\b/g)).toHaveLength(1);
  });

  it("nor in its words", () => {
    const words = Object.values(amDictionaries.th as Record<string, string>);
    for (const gone of ["เลือกโมเดลเอง", "ใช้กับรอบนี้", "ใช้เป็นค่าตั้งต้น", "เร็วและประหยัด", "แม่นที่สุด", "ตอนนี้องค์กรมีโมเดลในองค์กรตัวเดียว", "อัตโนมัติ (แนะนำ)"]) {
      expect(words.filter((w) => w.includes(gone))).toEqual([]);
    }
  });
});
