// FIX59 Part 1 — the project memory panel asks for ONE project.
//
// The check that matters is on the REQUEST, not on the pixels. A panel
// that renders only project A's memories because A happens to be the
// only project with any would pass a visual check and still be sending
// an unscoped query. FIX58 sealed projects off from each other in the
// SQL; a screen that asks without a `scope_id` re-opens exactly that,
// so this asserts the parameters that leave the browser.
//
// The server refuses `scope_type=project` without a `scope_id` with a
// 400, so the failure mode is a visible error rather than another
// project's facts on the page — the last test here pins that we surface
// it as an error and never as "nothing remembered".

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Memory } from "@/lib/types";

const listMemories = vi.fn();
const deleteMemory = vi.fn();
const pinMemory = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listMemories: (...a: unknown[]) => listMemories(...a),
    deleteMemory: (...a: unknown[]) => deleteMemory(...a),
    pinMemory: (...a: unknown[]) => pinMemory(...a),
  },
}));

vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));

const PROJECT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const PROJECT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function memory(id: string, content: string, scopeId: string): Memory {
  return {
    id,
    scope_type: "project",
    scope_id: scopeId,
    memory_type: "explicit_fact",
    content,
    source_type: "user_explicit",
    status: "active",
    confidence_score: 1,
    importance_score: 0.5,
    is_user_confirmed: true,
    is_pinned: false,
    sensitivity_level: "normal",
    use_count: 0,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

/** Import late so the api mock is in place first. */
async function renderPanel(projectId: string) {
  const { ProjectMemorySection } = await import(
    "@/components/chat/ProjectMemorySection"
  );
  render(<ProjectMemorySection projectId={projectId} />);
}

beforeEach(() => {
  // MemoryFormDialog renders a Modal, which reads a media query on
  // mount; jsdom has none. Same stub the other page tests use.
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  listMemories.mockReset();
  deleteMemory.mockReset();
  pinMemory.mockReset();
});

afterEach(cleanup);

describe("project memory panel", () => {
  it("asks for exactly one project's memories, by scope_id", async () => {
    listMemories.mockResolvedValue({
      memories: [memory("m-a", "A: the client contact is Somchai", PROJECT_A)],
      total: 1,
    });
    await renderPanel(PROJECT_A);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());

    const params = listMemories.mock.calls[0][0] as Record<string, unknown>;
    expect(params.scope_type).toBe("project");
    // The whole point. Without this the request means "every project".
    expect(params.scope_id).toBe(PROJECT_A);
    expect(params.scope_id).not.toBe(PROJECT_B);
  });

  it("re-queries with the new id when a different project is opened", async () => {
    listMemories.mockResolvedValue({ memories: [], total: 0 });
    await renderPanel(PROJECT_B);
    await waitFor(() => expect(listMemories).toHaveBeenCalled());
    const params = listMemories.mock.calls[0][0] as Record<string, unknown>;
    expect(params.scope_id).toBe(PROJECT_B);
  });

  it("renders only what the scoped request returned", async () => {
    listMemories.mockResolvedValue({
      memories: [memory("m-a", "A: the client contact is Somchai", PROJECT_A)],
      total: 1,
    });
    await renderPanel(PROJECT_A);
    await waitFor(() =>
      expect(screen.getByText(/the client contact is Somchai/)).toBeTruthy(),
    );
    expect(screen.queryByText(/Globex/)).toBeNull();
  });

  it("says nothing is remembered when the project genuinely has none", async () => {
    listMemories.mockResolvedValue({ memories: [], total: 0 });
    await renderPanel(PROJECT_A);
    await waitFor(() =>
      expect(screen.getByText("Nothing remembered in this project yet.")).toBeTruthy(),
    );
  });

  // An empty list and a failed load mean opposite things to someone
  // deciding whether their data is still there.
  it("shows a failure as a failure, never as an empty project", async () => {
    listMemories.mockRejectedValue(new Error("400"));
    await renderPanel(PROJECT_A);
    await waitFor(() =>
      expect(
        screen.getByText("Couldn't load this project's memories."),
      ).toBeTruthy(),
    );
    expect(
      screen.queryByText("Nothing remembered in this project yet."),
    ).toBeNull();
  });
});
