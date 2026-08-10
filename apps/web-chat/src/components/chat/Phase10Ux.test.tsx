// Phase 10 — archive, shortcuts cheatsheet, slash quick actions,
// in-app file preview.
//
// The load-bearing assertions here:
//   * slash only triggers on a bare leading token — a "/" mid-sentence
//     must never hijack the composer (that would break normal typing);
//   * the PDF preview iframe carries NO sandbox capabilities, so a
//     malicious PDF's embedded JS cannot run or reach this origin;
//   * office files preview through the EXISTING extraction pipeline and
//     say "still extracting" instead of showing a misleading blank;
//   * download survives next to preview.

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    getFileText: vi.fn(),
    setConversationArchived: vi.fn(),
    listArchivedConversations: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import {
  SLASH_COMMANDS,
  filterSlashCommands,
  shouldShowSlashMenu,
  slashQuery,
} from "@/lib/slashCommands";
import type { Conversation, FileAttachment } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { ConversationItem } from "./ConversationItem";
import { FilePreviewModal, previewKindFor } from "./FilePreviewModal";
import { SHORTCUTS, ShortcutsDialog, renderKey } from "./ShortcutsDialog";
import { SlashMenu } from "./SlashMenu";

const initialState = useChatStore.getState();

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  useChatStore.setState({ ...initialState }, true);
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

function conv(extra?: Partial<Conversation>): Conversation {
  return {
    id: "c1",
    title: "Conv c1",
    model_id: "gpt-4o-mini",
    message_count: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

function file(extra?: Partial<FileAttachment>): FileAttachment {
  return {
    id: "f1",
    filename: "report.pdf",
    content_type: "application/pdf",
    file_size_bytes: 1234,
    extraction_status: "completed",
    created_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

// ─── Task 1: archive ────────────────────────────────────────────

describe("archive", () => {
  const noop = () => {};

  it("offers Archive on a live chat and Unarchive on an archived one", () => {
    render(
      <ConversationItem
        conv={conv()}
        active={false}
        onSelect={noop}
        onRename={noop}
        onDelete={noop}
        onTogglePin={noop}
        onToggleArchive={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText(/conversation actions|more/i));
    expect(screen.getByText(dictionaries.en["conv.archive"])).toBeInTheDocument();

    cleanup();
    render(
      <ConversationItem
        conv={conv({ is_archived: true })}
        active={false}
        onSelect={noop}
        onRename={noop}
        onDelete={noop}
        onTogglePin={noop}
        onToggleArchive={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText(/conversation actions|more/i));
    expect(screen.getByText(dictionaries.en["conv.unarchive"])).toBeInTheDocument();
  });

  it("hides the action entirely when the host doesn't support it", () => {
    render(
      <ConversationItem
        conv={conv()}
        active={false}
        onSelect={noop}
        onRename={noop}
        onDelete={noop}
        onTogglePin={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText(/conversation actions|more/i));
    expect(screen.queryByText(dictionaries.en["conv.archive"])).toBeNull();
  });

  it("moves the row between the two lists optimistically", async () => {
    useChatStore.setState({ conversations: [conv()], archivedConversations: [] });
    mockApi.setConversationArchived.mockResolvedValue(conv({ is_archived: true }));

    await act(async () => {
      await useChatStore.getState().setConversationArchived("c1", true);
    });

    expect(useChatStore.getState().conversations).toHaveLength(0);
    expect(useChatStore.getState().archivedConversations.map((c) => c.id)).toEqual(["c1"]);

    await act(async () => {
      await useChatStore.getState().setConversationArchived("c1", false);
    });
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(["c1"]);
    expect(useChatStore.getState().archivedConversations).toHaveLength(0);
  });

  it("rolls the row back when the server rejects the archive", async () => {
    useChatStore.setState({ conversations: [conv()], archivedConversations: [] });
    mockApi.setConversationArchived.mockRejectedValue(new Error("500"));

    await act(async () => {
      await useChatStore.getState().setConversationArchived("c1", true);
    });

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(["c1"]);
    expect(useChatStore.getState().archivedConversations).toHaveLength(0);
    expect(useChatStore.getState().errorBanner).toBe(
      dictionaries.en["archive.error.failed"],
    );
  });
});

// ─── Task 2: shortcuts + slash ──────────────────────────────────

describe("shortcuts cheatsheet", () => {
  it("lists every binding with platform-correct keys", () => {
    render(<ShortcutsDialog open onClose={() => {}} />);
    for (const row of SHORTCUTS) {
      expect(screen.getByText(dictionaries.en[row.labelKey])).toBeInTheDocument();
    }
    expect(renderKey("Mod", true)).toBe("⌘");
    expect(renderKey("Mod", false)).toBe("Ctrl");
  });

  it("renders nothing when closed and closes on the backdrop", () => {
    const onClose = vi.fn();
    const { rerender } = render(<ShortcutsDialog open={false} onClose={onClose} />);
    expect(screen.queryByRole("dialog")).toBeNull();

    rerender(<ShortcutsDialog open onClose={onClose} />);
    fireEvent.click(screen.getByTestId("shortcuts-backdrop"));
    expect(onClose).toHaveBeenCalled();
  });

  it("localizes to Thai", () => {
    act(() => {
      useI18nStore.setState({ locale: "th" });
    });
    render(<ShortcutsDialog open onClose={() => {}} />);
    expect(screen.getByText(dictionaries.th["shortcuts.title"])).toBeInTheDocument();
  });
});

describe("slash trigger rule", () => {
  it("opens only for a bare leading slash token", () => {
    expect(shouldShowSlashMenu("/")).toBe(true);
    expect(shouldShowSlashMenu("/ima")).toBe(true);
    expect(shouldShowSlashMenu("/IMAGE")).toBe(true);
  });

  it("NEVER triggers mid-sentence — ordinary typing must be untouched", () => {
    for (const text of [
      "and/or",
      "open 24/7",
      "see https://example.com/docs",
      " /image", // leading space = prose, not a command
      "/image of a cat", // the token ended at the space
      "hello",
      "",
    ]) {
      expect(shouldShowSlashMenu(text), text).toBe(false);
    }
  });

  it("filters by keyword and alias, and empty query lists everything", () => {
    expect(slashQuery("/img")).toBe("img");
    expect(filterSlashCommands("").length).toBe(SLASH_COMMANDS.length);
    expect(filterSlashCommands("img").map((c) => c.id)).toEqual(["image"]);
    expect(filterSlashCommands("vid").map((c) => c.id)).toEqual(["video"]);
    expect(filterSlashCommands("zzz")).toEqual([]);
  });

  it("every command points at a real dictionary entry in BOTH locales", () => {
    for (const c of SLASH_COMMANDS) {
      expect(dictionaries.en[c.labelKey]).toBeTruthy();
      expect(dictionaries.th[c.labelKey]).toBeTruthy();
      expect(dictionaries.en[c.hintKey]).toBeTruthy();
      expect(dictionaries.th[c.hintKey]).toBeTruthy();
    }
  });
});

describe("slash menu", () => {
  it("renders the candidates and reports the picked one", () => {
    const onPick = vi.fn();
    render(
      <SlashMenu commands={filterSlashCommands("")} activeIndex={0} onPick={onPick} />,
    );
    expect(screen.getByTestId("slash-menu")).toBeInTheDocument();
    expect(screen.getByText(dictionaries.en["slash.image"])).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByText(dictionaries.en["slash.new"]));
    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "new" }),
    );
  });

  it("shows an empty state rather than vanishing on a bad token", () => {
    render(<SlashMenu commands={[]} activeIndex={0} onPick={() => {}} />);
    expect(screen.getByText(dictionaries.en["slash.empty"])).toBeInTheDocument();
  });
});

// ─── Task 3: file preview ───────────────────────────────────────

describe("preview kind routing", () => {
  it("routes by content type, falling back to the extension", () => {
    expect(previewKindFor({ content_type: "application/pdf", filename: "a.pdf" })).toBe("pdf");
    // Phase-7 reality: the browser often reports octet-stream.
    expect(previewKindFor({ content_type: "application/octet-stream", filename: "a.pdf" })).toBe("pdf");
    expect(previewKindFor({ content_type: "image/png", filename: "a.png" })).toBe("image");
    expect(previewKindFor({ content_type: "", filename: "a.docx" })).toBe("text");
    expect(previewKindFor({ content_type: "", filename: "a.xlsx" })).toBe("text");
    expect(previewKindFor({ content_type: "text/plain", filename: "a.txt" })).toBe("text");
    expect(previewKindFor({ content_type: "application/zip", filename: "a.zip" })).toBe("unsupported");
  });
});

describe("FilePreviewModal", () => {
  it("renders a PDF in a frame with NO sandbox capabilities", async () => {
    mockApi.fileObjectUrl.mockResolvedValue("blob:pdf");
    render(<FilePreviewModal file={file()} onClose={() => {}} />);

    const frame = await screen.findByTestId("preview-pdf");
    // The security assertion: empty sandbox = no scripts, no same-origin,
    // no popups, no top-navigation.
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame.getAttribute("src")).toBe("blob:pdf");
  });

  it("shows the already-extracted text for an office file", async () => {
    mockApi.getFileText.mockResolvedValue({
      extraction_status: "completed",
      text: "ยอดขายรวม 363,000 บาท",
      truncated: false,
      ocr: false,
    });
    render(
      <FilePreviewModal
        file={file({ filename: "sales.docx", content_type: "" })}
        onClose={() => {}}
      />,
    );

    expect(await screen.findByTestId("preview-text")).toHaveTextContent(
      "ยอดขายรวม 363,000 บาท",
    );
    // It reads the pipeline that already ran — no second extraction.
    expect(mockApi.fileObjectUrl).not.toHaveBeenCalled();
  });

  it("says the text is still being extracted instead of showing a blank box", async () => {
    mockApi.getFileText.mockResolvedValue({
      extraction_status: "processing",
      text: "",
      truncated: false,
      ocr: false,
    });
    render(
      <FilePreviewModal
        file={file({ filename: "scan.docx", content_type: "" })}
        onClose={() => {}}
      />,
    );
    expect(
      await screen.findByText(dictionaries.en["preview.extractPending"]),
    ).toBeInTheDocument();
  });

  it("keeps download working and closes on backdrop / Escape", async () => {
    mockApi.fileObjectUrl.mockResolvedValue("blob:pdf");
    const onClose = vi.fn();
    render(<FilePreviewModal file={file()} onClose={onClose} />);
    await screen.findByTestId("preview-pdf");

    fireEvent.click(screen.getByLabelText(dictionaries.en["preview.download"]));
    expect(mockApi.downloadFile).toHaveBeenCalledWith("f1", "report.pdf");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("preview-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("is honest about a type it cannot preview", async () => {
    render(
      <FilePreviewModal
        file={file({ filename: "archive.zip", content_type: "application/zip" })}
        onClose={() => {}}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByText(dictionaries.en["preview.unsupported"]),
      ).toBeInTheDocument(),
    );
  });

  it("surfaces a fetch failure instead of spinning forever", async () => {
    mockApi.fileObjectUrl.mockRejectedValue(new Error("500"));
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    expect(
      await screen.findByText(dictionaries.en["preview.failed"]),
    ).toBeInTheDocument();
  });
});
