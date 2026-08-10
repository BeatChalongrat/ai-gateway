// Phase 6 / C3 — edit mode, live re-render, versioning, persistence.
//
// The security-critical assertion in this file: after an EDIT, the
// preview still goes through the SAME C2 sandbox (sandbox attribute
// exactly "allow-scripts", exec CSP with connect-src 'none'). C3 adds
// no second render path, so editing cannot loosen the posture.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

import { Markdown } from "@/components/chat/Markdown";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { CanvasArtifactRow } from "@/lib/types";
import { useCanvasStore } from "@/stores/canvasStore";

import { CanvasPanel, CANVAS_EDIT_DEBOUNCE_MS } from "./CanvasPanel";
import {
  CanvasSourceProvider,
  artifactKeyFor,
} from "./CanvasSourceContext";

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

function openBound(content = "<h1>original</h1>") {
  act(() => {
    useCanvasStore.getState().openCanvas({ type: "html", content, ...BOUND });
  });
}

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
});

describe("C3 edit mode", () => {
  it("toggles Preview ↔ Code and edits the draft", () => {
    render(<CanvasPanel />);
    openBound();
    expect(screen.getByTestId("canvas-frame")).toBeInTheDocument();
    expect(screen.queryByTestId("canvas-editor")).toBeNull();

    fireEvent.click(screen.getByText(dictionaries.en["canvas.modeCode"]));
    const editor = screen.getByTestId("canvas-editor") as HTMLTextAreaElement;
    expect(editor.value).toBe("<h1>original</h1>");

    fireEvent.change(editor, { target: { value: "<h1>edited</h1>" } });
    expect(useCanvasStore.getState().draft).toBe("<h1>edited</h1>");
    expect(useCanvasStore.getState().dirty).toBe(true);

    fireEvent.click(screen.getByText(dictionaries.en["canvas.modePreview"]));
    expect(screen.getByTestId("canvas-frame")).toBeInTheDocument();
  });

  it("re-renders the preview from the edited draft after the debounce", () => {
    vi.useFakeTimers();
    try {
      render(<CanvasPanel />);
      openBound();
      act(() => {
        useCanvasStore.getState().setDraft("<h1>LIVE-EDIT</h1>");
      });
      // Before the debounce elapses the old srcdoc is still mounted.
      expect(screen.getByTestId("canvas-frame").getAttribute("srcdoc")).toContain(
        "original",
      );
      act(() => {
        vi.advanceTimersByTime(CANVAS_EDIT_DEBOUNCE_MS + 50);
      });
      expect(screen.getByTestId("canvas-frame").getAttribute("srcdoc")).toContain(
        "LIVE-EDIT",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("SECURITY — an edited artifact still renders through the C2 sandbox unchanged", () => {
    vi.useFakeTimers();
    try {
      render(<CanvasPanel />);
      openBound();
      act(() => {
        useCanvasStore
          .getState()
          .setDraft(
            '<script>window.parent.document.cookie</script><h1>evil edit</h1>',
          );
        vi.advanceTimersByTime(CANVAS_EDIT_DEBOUNCE_MS + 50);
      });
      const frame = screen.getByTestId("canvas-frame");
      // Exactly the C2 posture — no token added, no CSP relaxed.
      expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
      const srcdoc = frame.getAttribute("srcdoc") ?? "";
      expect(srcdoc).toContain("connect-src 'none'");
      expect(srcdoc).toContain("default-src 'none'");
      expect(srcdoc).not.toContain("allow-same-origin");
      // The edited script never entered the app's own DOM.
      expect(document.querySelector("h1")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("edited markdown/svg keep their static (script-free) render path", () => {
    vi.useFakeTimers();
    try {
      render(<CanvasPanel />);
      act(() => {
        useCanvasStore
          .getState()
          .openCanvas({ type: "svg", content: "<svg><rect/></svg>", ...BOUND });
        useCanvasStore.getState().setDraft('<svg onload="evil()"><circle/></svg>');
        vi.advanceTimersByTime(CANVAS_EDIT_DEBOUNCE_MS + 50);
      });
      const frame = screen.getByTestId("canvas-frame");
      expect(frame.getAttribute("sandbox")).toBe(""); // static, as in C1
      expect(frame.getAttribute("srcdoc")).not.toContain("onload");
    } finally {
      vi.useRealTimers();
    }
  });

  it("session-only mode (no conversation binding) hides save/history", () => {
    render(<CanvasPanel />);
    act(() => {
      useCanvasStore.getState().openCanvas({ type: "html", content: "<b>x</b>" });
    });
    expect(screen.queryByTestId("canvas-save")).toBeNull();
    expect(screen.queryByTestId("canvas-history-toggle")).toBeNull();
    expect(
      screen.getByText(dictionaries.en["canvas.sessionOnly"]),
    ).toBeInTheDocument();
    // …but editing still works in-session.
    fireEvent.click(screen.getByText(dictionaries.en["canvas.modeCode"]));
    expect(screen.getByTestId("canvas-editor")).toBeInTheDocument();
    expect(mockApi.listCanvasVersions).not.toHaveBeenCalled();
  });
});

describe("C3 versioning + persistence", () => {
  it("saves a new version and prepends it to the history", async () => {
    mockApi.saveCanvasArtifact.mockResolvedValue({
      artifact: row({ version: 2, content: "<h1>edited</h1>" }),
    });
    render(<CanvasPanel />);
    openBound();
    act(() => {
      useCanvasStore.getState().setDraft("<h1>edited</h1>");
    });
    await act(async () => {
      await useCanvasStore.getState().saveVersion();
    });

    expect(mockApi.saveCanvasArtifact).toHaveBeenCalledWith("conv-1", {
      artifact_key: "msg-1:abc",
      type: "html",
      title: undefined,
      content: "<h1>edited</h1>",
      message_id: "msg-1",
    });
    const st = useCanvasStore.getState();
    expect(st.dirty).toBe(false);
    expect(st.artifact?.content).toBe("<h1>edited</h1>");
    expect(st.versions[0].version).toBe(2);
  });

  it("hydrates the LATEST saved version on open — the edit survived the reload", async () => {
    mockApi.listCanvasVersions.mockResolvedValue({
      versions: [
        row({ version: 3, content: "<h1>saved v3</h1>" }),
        row({ version: 2 }),
        row({ version: 1 }),
      ],
    });
    render(<CanvasPanel />);
    await act(async () => {
      openBound("<h1>stale chat block</h1>");
      await Promise.resolve();
    });
    const st = useCanvasStore.getState();
    expect(st.artifact?.content).toBe("<h1>saved v3</h1>");
    expect(st.draft).toBe("<h1>saved v3</h1>");
    expect(st.versions).toHaveLength(3);
  });

  it("does NOT clobber unsaved edits when the version fetch lands late", async () => {
    let resolveFetch: (v: unknown) => void = () => {};
    mockApi.listCanvasVersions.mockReturnValue(
      new Promise((res) => {
        resolveFetch = res;
      }),
    );
    render(<CanvasPanel />);
    openBound("<h1>original</h1>");
    act(() => {
      useCanvasStore.getState().setDraft("<h1>my unsaved work</h1>");
    });
    await act(async () => {
      resolveFetch({ versions: [row({ version: 9, content: "<h1>server</h1>" })] });
      await Promise.resolve();
    });
    expect(useCanvasStore.getState().draft).toBe("<h1>my unsaved work</h1>");
    expect(useCanvasStore.getState().versions).toHaveLength(1);
  });

  it("reverts to an older version by appending a NEW version (history preserved)", async () => {
    mockApi.listCanvasVersions.mockResolvedValue({
      versions: [
        row({ version: 2, content: "<h1>v2</h1>" }),
        row({ version: 1, content: "<h1>v1</h1>" }),
      ],
    });
    mockApi.revertCanvasArtifact.mockResolvedValue({
      artifact: row({ version: 3, content: "<h1>v1</h1>" }),
    });
    render(<CanvasPanel />);
    await act(async () => {
      openBound();
      await Promise.resolve();
    });
    fireEvent.click(screen.getByTestId("canvas-history-toggle"));
    const restore = screen.getAllByText(dictionaries.en["canvas.revert"])[0];
    await act(async () => {
      fireEvent.click(restore);
      await Promise.resolve();
    });
    expect(mockApi.revertCanvasArtifact).toHaveBeenCalledWith("conv-1", "msg-1:abc", 1);
    const st = useCanvasStore.getState();
    expect(st.draft).toBe("<h1>v1</h1>");
    expect(st.versions[0].version).toBe(3);
    expect(st.versions).toHaveLength(3); // nothing deleted
  });

  it("surfaces a save failure instead of silently dropping the edit", async () => {
    mockApi.saveCanvasArtifact.mockRejectedValue(new Error("500"));
    render(<CanvasPanel />);
    openBound();
    act(() => {
      useCanvasStore.getState().setDraft("<h1>edited</h1>");
    });
    await act(async () => {
      await useCanvasStore.getState().saveVersion();
    });
    expect(screen.getByTestId("canvas-save-error")).toHaveTextContent(
      dictionaries.en["canvas.saveFailed"],
    );
    // The user's work is still in the buffer.
    expect(useCanvasStore.getState().draft).toBe("<h1>edited</h1>");
    expect(useCanvasStore.getState().dirty).toBe(true);
  });

  it("renders the C3 controls in Thai", () => {
    useI18nStore.setState({ locale: "th" });
    render(<CanvasPanel />);
    openBound();
    expect(
      screen.getByText(dictionaries.th["canvas.modeCode"]),
    ).toBeInTheDocument();
    expect(screen.getByText(dictionaries.th["canvas.save"])).toBeInTheDocument();
  });
});

describe("C3 artifact identity + chat binding", () => {
  it("derives a stable key per (message, block) and a distinct one per block", () => {
    const a = artifactKeyFor("msg-1", "<h1>a</h1>");
    expect(artifactKeyFor("msg-1", "<h1>a</h1>")).toBe(a);
    expect(artifactKeyFor("msg-1", "<h1>b</h1>")).not.toBe(a);
    expect(artifactKeyFor("msg-2", "<h1>a</h1>")).not.toBe(a);
    expect(artifactKeyFor(undefined, "<h1>a</h1>")).toContain("adhoc");
  });

  it("a code block inside a message opens the canvas BOUND to that conversation", () => {
    render(
      <CanvasSourceProvider conversationId="conv-9" messageId="msg-9">
        <Markdown>{"```html\n<h1>Doc</h1>\n```"}</Markdown>
      </CanvasSourceProvider>,
    );
    fireEvent.click(screen.getByLabelText(dictionaries.en["canvas.open"]));
    const a = useCanvasStore.getState().artifact;
    expect(a?.conversationId).toBe("conv-9");
    expect(a?.messageId).toBe("msg-9");
    expect(a?.artifactKey).toBe(artifactKeyFor("msg-9", "<h1>Doc</h1>\n"));
    expect(mockApi.listCanvasVersions).toHaveBeenCalledWith(
      "conv-9",
      a?.artifactKey,
    );
  });
});
