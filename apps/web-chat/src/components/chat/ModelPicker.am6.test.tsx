// AM6 — the agent's model menu is the chat's model menu. For that the chat's
// picker takes four optional props: a locked row that says nothing on itself
// (`quietGate`), one line under the search box (`notice`), rows that are not
// listed (`hidden`) and the last line of a group that counts them
// (`hiddenLine`). A caller that passes none of them — the chat — gets the
// picker it always got.
import type { ComponentProps } from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore, useT } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import { ModelPicker } from "./ModelSelector";

const model = (id: string, o: Partial<ModelEntry> = {}): ModelEntry => ({
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

// one in-house model that reads images and six that do not, as the owner counted them
const CATALOG: ModelEntry[] = [
  model("qwen3.8-27b-fp8", OWN),
  model("typhoon-text", { ...OWN, ...TEXT_ONLY }),
  model("pathumma-text", { ...OWN, ...TEXT_ONLY }),
  model("openthaigpt", { ...OWN, ...TEXT_ONLY }),
  model("wangchan", { ...OWN, ...TEXT_ONLY }),
  model("seallm", { ...OWN, ...TEXT_ONLY }),
  model("scb-text", { ...OWN, ...TEXT_ONLY }),
  model("gpt-4o"),
  model("gpt-4o-mini"),
  model("claude-sonnet", { provider: "anthropic" }),
  model("deepseek-v4-flash", { provider: "deepseek", ...TEXT_ONLY }),
  model("sora-video", { model_kind: "video" }),
];

const OUTSIDE = new Set(["gpt-4o", "gpt-4o-mini", "claude-sonnet"]);
const REASON = "องค์กรไม่อนุญาตให้ส่งเอกสารออกนอกองค์กร โมเดลนอกองค์กรจึงเลือกไม่ได้";
const WHY = "agent นี้ต้องใช้โมเดลที่อ่านภาพได้";
const readsImages = (m: ModelEntry) => m.capabilities.includes("vision");

type PickerProps = ComponentProps<typeof ModelPicker>;

function Picker(props: Partial<PickerProps>) {
  const t = useT();
  return (
    <div data-testid="picker">
      <ModelPicker models={CATALOG} activeId={null} isAuto onPickAuto={() => {}} onPickModel={() => {}} needsVision={false} t={t} {...props} />
    </div>
  );
}

const agentProps: Partial<PickerProps> = {
  chatOnly: true,
  gate: (m) => (OUTSIDE.has(m.id) ? REASON : undefined),
  quietGate: true,
  notice: REASON,
  hidden: (m) => !readsImages(m),
  hiddenLine: (n) => `ซ่อน ${n} ตัวที่ทำงานนี้ไม่ได้ · ${WHY}`,
};

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  useModelStore.setState({ models: CATALOG, loaded: true, loading: false, loadFailed: false } as never);
  window.matchMedia = ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
  window.localStorage.clear();
});
afterEach(cleanup);

interface Group {
  provider: string;
  count: string;
  rows: string[];
  hidden: string | null;
}

/** The provider groups as a person reads them, every "…more" opened. */
function groups(root: HTMLElement): Group[] {
  for (;;) {
    const more = within(root)
      .queryAllByRole("button")
      .find((b) => /^…อีก \d+ รายการ$/.test(b.textContent ?? ""));
    if (!more) break;
    fireEvent.click(more);
  }
  const out: Group[] = [];
  for (const key of ["full", "own"]) {
    const block = root.querySelector(`[data-testid="model-group-${key}"]`);
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

describe("AM6 — the chat asks for nothing new and gets the picker it had", () => {
  it("no line under the search box, no line about hidden models, the reason on the row", () => {
    render(<Picker needsVision />);
    const root = screen.getByTestId("picker");
    expect(within(root).queryByTestId("model-picker-notice")).toBeNull();
    expect(within(root).queryByTestId("model-group-hidden")).toBeNull();
    // the chat's own unavailable row: locked, and it says why on itself
    const locked = within(root).getAllByTestId("model-row-disabled");
    expect(locked.length).toBeGreaterThan(0);
    for (const r of locked) {
      expect(within(r).getByTestId("model-row-reason").textContent).toBe("อ่านรูปที่แนบไม่ได้");
      expect(r.getAttribute("title")).toBe("อ่านรูปที่แนบไม่ได้");
    }
  });

  it("a gate without quietGate still writes its reason on the row (AM5's callers)", () => {
    render(<Picker gate={agentProps.gate} />);
    const locked = screen.getAllByTestId("model-row-disabled");
    for (const r of locked) expect(within(r).getByTestId("model-row-reason").textContent).toBe(REASON);
  });
});

describe("AM6 — a row the organisation does not allow", () => {
  it("is the chat's unavailable row, with no reason on it and none on hover", () => {
    const chat = render(<Picker needsVision />);
    const want = (chat.getAllByTestId("model-row-disabled")[0] as HTMLElement).className;
    chat.unmount();

    render(<Picker {...agentProps} />);
    const locked = screen.getAllByTestId("model-row-disabled");
    expect(locked.map((r) => within(r).getByTestId("model-row-name").textContent).sort()).toEqual(["claude-sonnet", "gpt-4o", "gpt-4o-mini"]);
    for (const r of locked) {
      expect(r.className).toBe(want);
      expect(r.getAttribute("aria-disabled")).toBe("true");
      expect(within(r).queryByTestId("model-row-reason")).toBeNull();
      expect(r.hasAttribute("title")).toBe(false);
      expect(r.textContent).not.toContain(REASON);
    }
  });

  it("cannot be picked", () => {
    const onPickModel = vi.fn();
    render(<Picker {...agentProps} onPickModel={onPickModel} />);
    for (const r of screen.getAllByTestId("model-row-disabled")) fireEvent.click(r);
    expect(onPickModel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("qwen3.8-27b-fp8"));
    expect(onPickModel).toHaveBeenCalledTimes(1);
    expect(onPickModel.mock.calls[0]![0].id).toBe("qwen3.8-27b-fp8");
  });

  it("the reason is said once, under the search box", () => {
    render(<Picker {...agentProps} />);
    const root = screen.getByTestId("picker");
    const notice = within(root).getByTestId("model-picker-notice");
    expect(notice.textContent).toBe(REASON);
    expect((root.textContent ?? "").split(REASON)).toHaveLength(2);
    // in the header, with the search box: not among the rows
    expect(notice.parentElement?.querySelector("input")).toBeTruthy();
    expect(notice.closest('[role="option"]')).toBeNull();
  });

  it("the line is not there when no listed row is locked", () => {
    render(<Picker {...agentProps} gate={() => undefined} />);
    expect(screen.queryByTestId("model-picker-notice")).toBeNull();
    expect(screen.queryByTestId("model-row-disabled")).toBeNull();
    cleanup();
    // a search that leaves no locked row on the list
    render(<Picker {...agentProps} />);
    fireEvent.change(screen.getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "qwen" } });
    expect(screen.queryByTestId("model-row-disabled")).toBeNull();
    expect(screen.queryByTestId("model-picker-notice")).toBeNull();
  });
});

describe("AM6 — a model that cannot do the job", () => {
  it("is not listed, and the group's last line says how many and why", () => {
    render(<Picker {...agentProps} />);
    const own = groups(screen.getByTestId("picker")).find((g) => g.provider === "sranai")!;
    expect(own.rows).toEqual(["qwen3.8-27b-fp8"]);
    expect(own.hidden).toBe(`ซ่อน 6 ตัวที่ทำงานนี้ไม่ได้ · ${WHY}`);
    // the last thing in the group
    const line = screen.getAllByTestId("model-group-hidden").find((l) => l.textContent === own.hidden)!;
    expect(line.nextElementSibling).toBeNull();
    expect(line.closest('[role="option"]')).toBeNull();
  });

  it("a group with nothing left keeps its header and the line", () => {
    render(<Picker {...agentProps} />);
    const deepseek = groups(screen.getByTestId("picker")).find((g) => g.provider === "deepseek")!;
    expect(deepseek.rows).toEqual([]);
    expect(deepseek.hidden).toBe(`ซ่อน 1 ตัวที่ทำงานนี้ไม่ได้ · ${WHY}`);
  });

  it("a group with nothing hidden has no such line", () => {
    render(<Picker {...agentProps} />);
    const openai = groups(screen.getByTestId("picker")).find((g) => g.provider === "openai")!;
    expect(openai.rows).toEqual(["gpt-4o", "gpt-4o-mini"]);
    expect(openai.hidden).toBeNull();
  });

  it("rows listed + rows hidden = the rows the chat's picker lists, group by group, in the same order", () => {
    const chat = render(<Picker />);
    const want = groups(chat.getByTestId("picker")).map((g) => ({ provider: g.provider, count: g.count, rows: g.rows.length }));
    chat.unmount();
    expect(want.find((g) => g.provider === "sranai")).toEqual({ provider: "sranai", count: "7", rows: 7 });

    render(<Picker {...agentProps} />);
    const got = groups(screen.getByTestId("picker")).map((g) => ({ provider: g.provider, count: g.count, rows: g.rows.length + hiddenCount(g.hidden) }));
    expect(got).toEqual(want);
  });

  it("while searching, the line counts what the search found", () => {
    const chat = render(<Picker />);
    fireEvent.change(chat.getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "text" } });
    const want = groups(chat.getByTestId("picker")).map((g) => ({ provider: g.provider, rows: g.rows.length }));
    chat.unmount();
    expect(want).toEqual([{ provider: "sranai", rows: 3 }]);

    render(<Picker {...agentProps} />);
    fireEvent.change(screen.getByPlaceholderText("ค้นหาโมเดล…"), { target: { value: "text" } });
    const got = groups(screen.getByTestId("picker"));
    expect(got.map((g) => ({ provider: g.provider, rows: g.rows.length + hiddenCount(g.hidden) }))).toEqual(want);
    expect(got[0]!.hidden).toBe(`ซ่อน 3 ตัวที่ทำงานนี้ไม่ได้ · ${WHY}`);
  });

  it("Popular and Recently used list no hidden model, and lock what the organisation does not allow", () => {
    window.localStorage.setItem("sran-model-usage", JSON.stringify({ "typhoon-text": 9, "gpt-4o": 5, "qwen3.8-27b-fp8": 2 }));
    window.localStorage.setItem("sran-recent-models", JSON.stringify(["seallm", "gpt-4o-mini", "qwen3.8-27b-fp8"]));
    render(<Picker {...agentProps} />);
    const section = (title: string) => screen.getByText(title).closest("div.border-t") as HTMLElement;
    const names = (el: HTMLElement) => within(el).getAllByTestId("model-row-name").map((n) => n.textContent);
    const popular = section("ยอดนิยม");
    expect(names(popular)).toEqual(["gpt-4o", "qwen3.8-27b-fp8"]);
    expect(within(popular).getAllByTestId("model-row-disabled").map((r) => within(r).getByTestId("model-row-name").textContent)).toEqual(["gpt-4o"]);
    expect(within(popular).queryByTestId("model-row-reason")).toBeNull();
    expect(names(section("ใช้ล่าสุด"))).toEqual(["gpt-4o-mini", "qwen3.8-27b-fp8"]);
  });
});
