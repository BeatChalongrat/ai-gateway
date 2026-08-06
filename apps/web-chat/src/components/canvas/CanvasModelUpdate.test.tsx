// Phase 8 Task 3 — model-driven canvas updates, client side.
//
// Two behaviours make "tell the AI to change the artifact" work:
//   1. ADOPT — opening a block for the first time persists it as
//      version 1, so the artifact has a server-side key the model can
//      address (before this, an unsaved block was invisible to the AI).
//   2. REFRESH — after an assistant turn, an open panel re-reads its
//      lineage so a version the MODEL wrote replaces what's on screen.
//
// The security assertion: a model-written version renders through the
// SAME C2 sandbox as everything else — the panel has one render path.

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    listCanvasVersions: vi.fn(),
    saveCanvasArtifact: vi.fn(),
    revertCanvasArtifact: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";
import type { CanvasArtifactRow } from "@/lib/types";
import { useCanvasStore } from "@/stores/canvasStore";

import { CanvasPanel } from "./CanvasPanel";
import { CANVAS_EXEC_SANDBOX } from "./canvasUtil";

const BOUND = {
  conversationId: "conv-1",
  messageId: "msg-1",
  artifactKey: "msg-1:abc",
};

function row(over: Partial<CanvasArtifactRow> = {}): CanvasArtifactRow {
  return {
    id: `row-${over.version ?? 1}`,
    conversation_id: "conv-1",
    message_id: "msg-1",
    artifact_key: "msg-1:abc",
    type: "html",
    title: null,
    content: "<h1>v1</h1>",
    version: 1,
    created_at: "2026-08-06T00:00:00Z",
    ...over,
  };
}

function openBound(content = "<button>red</button>") {
  act(() => {
    useCanvasStore.getState().openCanvas({ type: "html", content, ...BOUND });
  });
}

const settle = () => act(async () => { await Promise.resolve(); });

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useCanvasStore.setState({
    open: false,
    artifact: null,
    draft: "",
    mode: "preview",
    dirty: false,
    versions: [],
    versionsLoading: false,
    saving: false,
    error: null,
  });
  mockApi.listCanvasVersions.mockResolvedValue({ versions: [] });
  mockApi.saveCanvasArtifact.mockResolvedValue({ artifact: row() });
});

describe("adopt on first open", () => {
  it("persists the original block as version 1 so the model can address it", async () => {
    render(<CanvasPanel />);
    openBound();
    await settle();

    expect(mockApi.saveCanvasArtifact).toHaveBeenCalledWith("conv-1", {
      artifact_key: "msg-1:abc",
      type: "html",
      title: undefined,
      content: "<button>red</button>",
      message_id: "msg-1",
    });
    expect(useCanvasStore.getState().versions).toHaveLength(1);
  });

  it("does NOT adopt when the artifact already has a lineage", async () => {
    mockApi.listCanvasVersions.mockResolvedValue({
      versions: [row({ version: 3, content: "<button>green</button>" })],
    });
    render(<CanvasPanel />);
    openBound();
    await settle();

    expect(mockApi.saveCanvasArtifact).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().artifact?.content).toBe(
      "<button>green</button>",
    );
  });

  it("does NOT adopt an unbound (ad-hoc) artifact — nothing to bind to", async () => {
    act(() => {
      useCanvasStore
        .getState()
        .openCanvas({ type: "html", content: "<p>hi</p>" });
    });
    await settle();

    expect(mockApi.listCanvasVersions).not.toHaveBeenCalled();
    expect(mockApi.saveCanvasArtifact).not.toHaveBeenCalled();
  });
});

describe("refresh after an assistant turn", () => {
  it("adopts the version the model wrote and renders it in the same sandbox", async () => {
    render(<CanvasPanel />);
    openBound();
    await settle();

    // The model called update_canvas_artifact during the next turn.
    mockApi.listCanvasVersions.mockResolvedValue({
      versions: [
        row({ version: 2, content: "<button style='color:green'>ok</button>" }),
        row({ version: 1 }),
      ],
    });
    act(() => {
      useCanvasStore.getState().refreshIfOpen();
    });
    await settle();

    const st = useCanvasStore.getState();
    expect(st.artifact?.content).toBe("<button style='color:green'>ok</button>");
    expect(st.draft).toBe("<button style='color:green'>ok</button>");
    expect(st.versions[0].version).toBe(2);

    // Security: still the C2 executable sandbox, unchanged.
    const frame = screen.getByTestId("canvas-frame");
    expect(frame.getAttribute("sandbox")).toBe(CANVAS_EXEC_SANDBOX);
    expect(frame.getAttribute("srcdoc")).toContain("connect-src 'none'");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("never clobbers an edit the user is in the middle of", async () => {
    render(<CanvasPanel />);
    openBound();
    await settle();

    act(() => {
      useCanvasStore.getState().setDraft("<button>MY EDIT</button>");
    });
    mockApi.listCanvasVersions.mockResolvedValue({
      versions: [row({ version: 2, content: "<button>model</button>" })],
    });
    act(() => {
      useCanvasStore.getState().refreshIfOpen();
    });
    await settle();

    expect(useCanvasStore.getState().draft).toBe("<button>MY EDIT</button>");
  });

  it("is a no-op when the panel is closed", async () => {
    act(() => {
      useCanvasStore.getState().refreshIfOpen();
    });
    await settle();
    expect(mockApi.listCanvasVersions).not.toHaveBeenCalled();
  });
});
