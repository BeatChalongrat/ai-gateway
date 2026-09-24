// FIX132 D1 — artifact versions never started on the real server.
//
// Live walkthrough (scripts/probes/FIX132/results/lanes/lane-ux/probe/d1.json):
// a [FIX132] chat, deepseek-v4-flash writes one ```html block, the page opens
// it in the canvas. The page asks GET …/artifacts/{key}/versions; chat-edge
// answers 404 for a key with no rows ("a key only exists through its
// versions", CanvasArtifactService.ListVersions). The store only adopted the
// block as version 1 on a 200 with an EMPTY list — which the server never
// sends — so the 404 fell into the "history failed to load" catch, nothing
// was saved, the model's "## Canvas Artifacts" context stayed empty, and the
// follow-up "make the button green" came back as a second, unrelated code
// block. The rail said "no saved versions" forever. The live DB had 0
// page-made canvas_artifacts rows (every row there came from Go tests).
// The existing canvas tests mocked `{ versions: [] }`, a reply the server
// does not give.
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, ApiErr } = vi.hoisted(() => {
  class ApiErr extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { ApiErr, mockApi: { listCanvasVersions: vi.fn(), saveCanvasArtifact: vi.fn(), revertCanvasArtifact: vi.fn() } };
});
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: ApiErr }));

import type { CanvasArtifactRow } from "@/lib/types";

import { useCanvasStore } from "./canvasStore";

const BOUND = { conversationId: "conv-1", messageId: "msg-1", artifactKey: "msg-1:abc" };
const row = (over: Partial<CanvasArtifactRow> = {}): CanvasArtifactRow => ({
  id: `row-${over.version ?? 1}`, conversation_id: "conv-1", message_id: "msg-1", artifact_key: "msg-1:abc",
  type: "html", title: null, content: "<button>blue</button>", version: 1, created_at: "2026-09-24T00:00:00Z", ...over,
});
const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
// What chat-edge really answers for a key that has no versions yet.
const NOT_FOUND = () => new ApiErr("NOT_FOUND", "canvas artifact not found", 404);

beforeEach(() => {
  vi.clearAllMocks();
  useCanvasStore.setState({ open: false, artifact: null, draft: "", mode: "preview", dirty: false, versions: [], versionsLoading: false, saving: false, error: null });
});

describe("FIX132 D1 — the first open adopts v1 on the server's real 'no versions yet' reply", () => {
  it("404 NOT_FOUND on the versions list → the block is saved as version 1", async () => {
    mockApi.listCanvasVersions.mockRejectedValue(NOT_FOUND());
    mockApi.saveCanvasArtifact.mockResolvedValue({ artifact: row() });
    useCanvasStore.getState().openCanvas({ type: "html", content: "<button>blue</button>", ...BOUND });
    await settle();
    expect(mockApi.saveCanvasArtifact).toHaveBeenCalledTimes(1);
    expect(mockApi.saveCanvasArtifact).toHaveBeenCalledWith("conv-1", {
      artifact_key: "msg-1:abc", type: "html", title: undefined, content: "<button>blue</button>", message_id: "msg-1",
    });
    expect(useCanvasStore.getState().versions.map((v) => v.version)).toEqual([1]);
    expect(useCanvasStore.getState().versionsLoading).toBe(false);
  });

  it("then the model's v2 (after the turn) replaces what is shown, and the rail lists v2, v1", async () => {
    mockApi.listCanvasVersions.mockRejectedValueOnce(NOT_FOUND());
    mockApi.saveCanvasArtifact.mockResolvedValue({ artifact: row() });
    useCanvasStore.getState().openCanvas({ type: "html", content: "<button>blue</button>", ...BOUND });
    await settle();
    mockApi.listCanvasVersions.mockResolvedValue({ versions: [row({ version: 2, content: "<button>green</button>" }), row()] });
    useCanvasStore.getState().refreshIfOpen();
    await settle();
    const s = useCanvasStore.getState();
    expect(s.artifact?.content).toBe("<button>green</button>");
    expect(s.draft).toBe("<button>green</button>");
    expect(s.versions.map((v) => v.version)).toEqual([2, 1]);
  });

  it("any other failure still leaves the preview alone and adopts nothing", async () => {
    mockApi.listCanvasVersions.mockRejectedValue(new ApiErr("INTERNAL_ERROR", "internal error", 500));
    useCanvasStore.getState().openCanvas({ type: "html", content: "<button>blue</button>", ...BOUND });
    await settle();
    expect(mockApi.saveCanvasArtifact).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().artifact?.content).toBe("<button>blue</button>");
    expect(useCanvasStore.getState().versionsLoading).toBe(false);
  });

  it("an open that races the first turn's refresh adopts once, not twice", async () => {
    mockApi.listCanvasVersions.mockRejectedValue(NOT_FOUND());
    let release!: (v: unknown) => void;
    mockApi.saveCanvasArtifact.mockReturnValue(new Promise((r) => (release = r)));
    useCanvasStore.getState().openCanvas({ type: "html", content: "<button>blue</button>", ...BOUND });
    await settle();
    useCanvasStore.getState().refreshIfOpen();
    await settle();
    release({ artifact: row() });
    await settle();
    expect(mockApi.saveCanvasArtifact).toHaveBeenCalledTimes(1);
  });
});
