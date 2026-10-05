// AM10 F — the cloud tasks list and its run history read in the app's
// language. The row's dates line, the paused notice, the buttons, the run
// headlines and the tags were English whatever the language was.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const data = vi.hoisted(() => ({
  tasks: [] as unknown[],
  runs: [] as unknown[],
}));

vi.mock("@/lib/api", () => ({
  api: {
    listCloudTasks: vi.fn(async () => ({ tasks: data.tasks })),
    createCloudTask: vi.fn(),
    setCloudTaskEnabled: vi.fn(),
    deleteCloudTask: vi.fn(),
    listCloudTaskRuns: vi.fn(async () => ({ runs: data.runs })),
  },
}));
vi.mock("@/stores/chatStore", () => {
  const st = {
    conversations: [] as unknown[],
    conversationsLoaded: true,
    activeConversationId: null,
    loadConversations: vi.fn(),
  };
  return { useChatStore: (sel: (s: typeof st) => unknown) => sel(st) };
});

import { useI18nStore } from "@/lib/i18n";

import { CloudTasksSection } from "./CloudTasksSection";

/** Latin words (3+ letters) left in a render once `keep` is taken out. */
function englishLeft(text: string, keep: string[] = []): string[] {
  let s = text;
  for (const k of keep) s = s.split(k).join(" ");
  return s.match(/[A-Za-z][A-Za-z'’]{2,}/g) ?? [];
}

const task = (over: Record<string, unknown>) => ({
  id: "t1",
  conversation_id: "c1",
  name: "สรุปประจำวัน",
  prompt: "สรุป",
  schedule_kind: "interval",
  interval_seconds: 3600,
  next_run_at: "2026-10-02T06:00:00Z",
  enabled: true,
  max_runs: 5,
  run_count: 2,
  last_run_at: "2026-10-01T06:00:00Z",
  paused_reason: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  ...over,
});

afterEach(cleanup);
beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  // Modal reads a media query on mount; jsdom has no matchMedia.
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => false),
  })) as unknown as typeof window.matchMedia;
});

describe("cloud tasks in Thai", () => {
  it("the rows: dates line, switch label, buttons and every paused notice", async () => {
    data.tasks = [
      task({ id: "t1" }),
      task({ id: "t2", paused_reason: "no_balance", enabled: false }),
      task({ id: "t3", paused_reason: "max_runs", enabled: false, interval_seconds: 172800 }),
      task({ id: "t4", paused_reason: "revoked", enabled: false, interval_seconds: 7200, last_run_at: null }),
      task({ id: "t5", interval_seconds: 90, max_runs: null }),
    ];
    render(<CloudTasksSection standalone openCreateNonce={0} onRequestCreate={() => {}} />);
    const section = await screen.findByTestId("cloud-tasks-section");
    await waitFor(() => expect(section.querySelectorAll("h3").length).toBe(5));
    expect(englishLeft(section.textContent ?? "")).toEqual([]);
    for (const el of Array.from(section.querySelectorAll("[aria-label]"))) {
      expect(englishLeft(el.getAttribute("aria-label") ?? "")).toEqual([]);
    }
  });

  it("the run history: title, every headline and the tags", async () => {
    data.tasks = [task({ id: "t1" })];
    data.runs = [
      { id: "r1", task_id: "t1", fired_at: "2026-10-01T06:00:00Z", outcome: "ok", tokens: 120, policy_action: "mask" },
      { id: "r2", task_id: "t1", fired_at: "2026-10-01T05:00:00Z", outcome: "ok", skip_reason: "tool_withheld" },
      { id: "r3", task_id: "t1", fired_at: "2026-10-01T04:00:00Z", outcome: "refused_revoked" },
      { id: "r4", task_id: "t1", fired_at: "2026-10-01T03:00:00Z", outcome: "refused_no_balance" },
      { id: "r5", task_id: "t1", fired_at: "2026-10-01T02:00:00Z", outcome: "error" },
    ];
    render(<CloudTasksSection standalone openCreateNonce={0} onRequestCreate={() => {}} />);
    const section = await screen.findByTestId("cloud-tasks-section");
    await waitFor(() => expect(section.querySelectorAll("h3").length).toBe(1));
    // A row is a <section> inside the list's own <section>; the header's
    // "new task" button sits outside every row.
    const buttons = Array.from(section.querySelectorAll("section section button"));
    // The first plain button of the row opens the history.
    fireEvent.click(buttons.find((b) => !b.getAttribute("role")) as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.querySelectorAll("li").length).toBe(5));
    // DLP is the feature's name in both dictionaries.
    expect(englishLeft(dialog.textContent ?? "", ["DLP"])).toEqual([]);
  });

  it("reads in English when the app is in English", async () => {
    useI18nStore.setState({ locale: "en" });
    data.tasks = [task({ id: "t2", paused_reason: "no_balance", enabled: false })];
    render(<CloudTasksSection standalone openCreateNonce={0} onRequestCreate={() => {}} />);
    const section = await screen.findByTestId("cloud-tasks-section");
    await waitFor(() => expect(section.querySelectorAll("h3").length).toBe(1));
    expect(section.textContent).toContain("Paused — out of credits. Top up, then re-enable.");
    expect(section.textContent).toContain("Runs: 2 / 5");
    expect(screen.getByText("History")).toBeTruthy();
    expect(screen.getByText("Re-enable")).toBeTruthy();
  });
});
