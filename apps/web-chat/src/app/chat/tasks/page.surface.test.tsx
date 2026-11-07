// FIX34.1 Part F (F2) — /chat/tasks is a different page per surface.
//
//   web:     cloud tasks ARE the page — no kind chooser, no greyed-out
//            "needs the desktop app" card, "New task" opens the cloud form.
//   desktop: unchanged — both sections, and "New task" asks which kind.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ hosted: false }));

vi.mock("@/hooks/useHostedMode", () => ({ useHostedMode: () => state.hosted }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/tasks",
}));
vi.mock("@/lib/api", () => ({
  api: {
    listCloudTasks: vi.fn(async () => ({ tasks: [] })),
    createCloudTask: vi.fn(),
    setCloudTaskEnabled: vi.fn(),
    deleteCloudTask: vi.fn(),
    listCloudTaskRuns: vi.fn(async () => ({ runs: [] })),
  },
}));
vi.mock("@/lib/hostApi", () => ({
  hostApi: {
    listTasks: vi.fn(async () => []),
    taskRuns: vi.fn(async () => []),
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

import ScheduledTasksPage from "./page";

afterEach(cleanup);
beforeEach(() => {
  state.hosted = false;
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

describe("/chat/tasks per surface", () => {
  it("web: cloud section is the whole page — no chooser, no desktop-only card", async () => {
    render(<ScheduledTasksPage />);
    const newTask = await screen.findByTestId("cloud-tasks-new");

    expect(screen.getByTestId("cloud-tasks-section").dataset.standalone).toBe("true");
    // Nothing about the other kind, and nothing pointing at the desktop app.
    expect(screen.queryByText("On this device")).toBeNull();
    expect(screen.queryByText("Where should this task run?")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Desktop app/i);
    expect(document.body.textContent).not.toMatch(/not in the browser/i);

    // "New task" goes straight to the cloud form — there is no kind to pick.
    fireEvent.click(newTask);
    expect(await screen.findByText("New cloud task")).toBeTruthy();
    expect(screen.queryByText("Where should this task run?")).toBeNull();
  });

  it("desktop: both sections, and 'New task' asks which kind", async () => {
    state.hosted = true;
    render(<ScheduledTasksPage />);
    const newTask = await screen.findByTestId("cloud-tasks-new");

    expect(screen.getByTestId("cloud-tasks-section").dataset.standalone).toBe("false");
    await waitFor(() => expect(screen.getAllByText("On this device").length).toBeGreaterThan(0));

    fireEvent.click(newTask);
    expect(await screen.findByText("Where should this task run?")).toBeTruthy();
    // Both kinds are real choices on the desktop — no greyed-out card.
    expect(screen.getByText("Runs only while the SRAN Desktop app is open. Can use local files.")).toBeTruthy();
  });
});
