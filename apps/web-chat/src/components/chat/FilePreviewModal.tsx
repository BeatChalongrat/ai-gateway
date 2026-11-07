"use client";

// Phase 10 Task 3 — in-app file preview.
//
// Until now a non-image attachment was a download card and nothing else:
// to read a PDF you had to save it and leave the app. This opens the
// file in place, choosing the cheapest honest renderer per type:
//
//   PDF     → the browser's own viewer inside a SANDBOXED iframe fed by
//             a blob: URL. No pdf.js dependency: every target browser
//             ships a viewer, and a blob URL keeps the authed fetch
//             (cookie + 401-refresh) that a bare <iframe src> would lose.
//   image   → the same authed object URL, shown full-size.
//   office  → Phase 16: the STRUCTURE, from GET /files/{id}/preview-data
//             — a spreadsheet as a grid with sheet tabs and paged rows,
//             Word as ordered blocks, a deck slide by slide. The flat
//             text stays the fallback for every case that route cannot
//             serve (415 for a type it does not read, 503 when the
//             sandbox is unavailable, or any error at all).
//   text    → the text the extraction pipeline ALREADY produced
//             (GET /files/{id}/text). No fidelity rendering: this is a
//             reader, and the extracted text is exactly what the model
//             sees, which makes it useful for checking what was read.
//   anything else → an honest "no preview, download it" message.
//
// SECURITY — the PDF iframe:
//   * sandbox="" — no allow-scripts, no allow-same-origin, no
//     allow-popups, no allow-top-navigation. Even a malicious PDF with
//     embedded JS cannot run script, reach this origin's cookies, or
//     navigate the tab. (The browser's built-in viewer renders under
//     the sandbox; PDF JavaScript is what the sandbox neutralises.)
//   * the source is a blob: URL minted from bytes we already fetched —
//     it never points at a remote origin.
// This mirrors the Canvas C1 posture (a static frame gets NO
// capabilities) and must not be loosened.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Download, X } from "lucide-react";

import { SidePane } from "@/components/ui/SidePane";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { api } from "@/lib/api";
import { isImageMime } from "@/lib/fileUtil";
import { useT } from "@/lib/i18n";
import type {
  DocumentPreview,
  SheetPreview,
  SlidesPreview,
} from "@/lib/previewData";
import { richPreviewKindFor } from "@/lib/previewData";
import type { FileAttachment } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import {
  DocumentView,
  PREVIEW_PAGE_BLOCKS,
  PREVIEW_PAGE_ROWS,
  PREVIEW_PAGE_SLIDES,
  SheetGrid,
  SlidesView,
} from "./RichPreview";

/** Static sandbox for the PDF frame — no capabilities at all. */
export const PDF_SANDBOX = "";

export type PreviewKind = "image" | "pdf" | "text" | "unsupported";

/** Which renderer a file gets. Content type first (the server's word),
 *  then the extension, because browser-reported types are often empty
 *  or application/octet-stream (see the Phase 7 upload gate). */
export function previewKindFor(file: {
  content_type: string;
  filename: string;
}): PreviewKind {
  const ct = (file.content_type || "").toLowerCase();
  const ext = file.filename.toLowerCase().split(".").pop() ?? "";

  if (isImageMime(ct) || ["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) {
    return "image";
  }
  if (ct === "application/pdf" || ext === "pdf") return "pdf";
  // Office + plain-text files read through the extraction pipeline.
  if (
    ct.startsWith("text/") ||
    ["docx", "xlsx", "pptx", "csv", "txt", "md", "markdown", "json", "log"].includes(ext)
  ) {
    return "text";
  }
  return "unsupported";
}

/** The preview itself — loaders + renderers, no frame. Used by the docked
 *  pane (FilePreviewPane) and by the modal fallback (FilePreviewModal). */
export function FilePreviewBody({ file }: { file: FileAttachment }) {
  const t = useT();
  const kind = previewKindFor(file);
  const rich = richPreviewKindFor(file);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(kind !== "unsupported");
  const [sheet, setSheet] = useState<SheetPreview | null>(null);
  const [doc, setDoc] = useState<DocumentPreview | null>(null);
  const [deck, setDeck] = useState<SlidesPreview | null>(null);
  const [paging, setPaging] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;

    async function loadText() {
      const res = await api.getFileText(file.id);
      if (cancelled) return;
      if (res.extraction_status !== "completed") {
        // Still extracting (or nothing to extract) — say so rather
        // than showing an empty box the user reads as "it's blank".
        setPending(true);
      } else {
        setText(res.text);
      }
    }

    /** Try the structured read; report whether it produced anything. */
    async function loadRich(): Promise<boolean> {
      try {
        const data = await api.getFilePreviewData(file.id, {
          limit:
            rich === "sheet"
              ? PREVIEW_PAGE_ROWS
              : rich === "document"
                ? PREVIEW_PAGE_BLOCKS
                : PREVIEW_PAGE_SLIDES,
        });
        if (cancelled) return true;
        if (data.kind === "sheet") setSheet(data.sheet);
        else if (data.kind === "document") setDoc(data.document);
        else setDeck(data.slides);
        return true;
      } catch {
        // 415 (type has no structured view), 503 (no sandbox on this
        // deployment) or anything else — the flat text still works, so
        // the preview degrades instead of failing.
        return false;
      }
    }

    async function load() {
      try {
        if (kind === "image" || kind === "pdf") {
          url = await api.fileObjectUrl(file.id);
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          setObjectUrl(url);
        } else if (kind === "text") {
          if (rich && (await loadRich())) return;
          if (cancelled) return;
          await loadText();
        }
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();

    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [file.id, kind, rich]);

  /** Swap to another sheet of the same workbook. Replaces the window. */
  const selectSheet = useCallback(
    async (index: number) => {
      setPaging(true);
      try {
        const data = await api.getFilePreviewData(file.id, {
          sheet: index,
          limit: PREVIEW_PAGE_ROWS,
        });
        if (data.kind === "sheet") setSheet(data.sheet);
      } catch {
        // Keep the sheet already on screen; the tab just doesn't move.
      } finally {
        setPaging(false);
      }
    },
    [file.id],
  );

  /** Append the next window. The accumulated view keeps its ORIGINAL
   *  offset so row numbers stay true after several pages. */
  const loadMore = useCallback(async () => {
    setPaging(true);
    try {
      if (sheet) {
        const data = await api.getFilePreviewData(file.id, {
          sheet: sheet.index,
          offset: sheet.offset + sheet.rows.length,
          limit: PREVIEW_PAGE_ROWS,
        });
        if (data.kind === "sheet") {
          setSheet({
            ...data.sheet,
            offset: sheet.offset,
            rows: [...sheet.rows, ...data.sheet.rows],
          });
        }
      } else if (doc) {
        const data = await api.getFilePreviewData(file.id, {
          offset: doc.offset + doc.blocks.length,
          limit: PREVIEW_PAGE_BLOCKS,
        });
        if (data.kind === "document") {
          setDoc({
            ...data.document,
            offset: doc.offset,
            blocks: [...doc.blocks, ...data.document.blocks],
          });
        }
      } else if (deck) {
        const data = await api.getFilePreviewData(file.id, {
          offset: deck.offset + deck.slides.length,
          limit: PREVIEW_PAGE_SLIDES,
        });
        if (data.kind === "slides") {
          setDeck({
            ...data.slides,
            offset: deck.offset,
            slides: [...deck.slides, ...data.slides.slides],
          });
        }
      }
    } catch {
      // Leave what is already on screen; the button can be pressed again.
    } finally {
      setPaging(false);
    }
  }, [file.id, sheet, doc, deck]);

  return (
        <div className="min-h-0 flex-1 overflow-auto bg-bg-base">
          {loading ? (
            <p className="p-6 text-center text-sm text-text-muted">
              {t("preview.loading")}
            </p>
          ) : error ? (
            <p className="p-6 text-center text-sm text-status-danger">
              {t("preview.failed")}
            </p>
          ) : sheet ? (
            <SheetGrid
              sheet={sheet}
              onSelectSheet={selectSheet}
              onLoadMore={loadMore}
              loading={paging}
            />
          ) : doc ? (
            <DocumentView doc={doc} onLoadMore={loadMore} loading={paging} />
          ) : deck ? (
            <SlidesView deck={deck} onLoadMore={loadMore} loading={paging} />
          ) : kind === "unsupported" ? (
            <p className="p-6 text-center text-sm text-text-muted">
              {t("preview.unsupported")}
            </p>
          ) : kind === "image" && objectUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={objectUrl}
              alt={file.filename}
              data-testid="preview-image"
              className="mx-auto max-h-full max-w-full object-contain"
            />
          ) : kind === "pdf" && objectUrl ? (
            <iframe
              src={objectUrl}
              title={file.filename}
              data-testid="preview-pdf"
              // NO capabilities: a PDF's embedded JavaScript must not run,
              // and the frame must not reach this origin. See the header.
              sandbox={PDF_SANDBOX}
              className="h-full min-h-[60vh] w-full border-0 bg-white"
            />
          ) : pending ? (
            <p className="p-6 text-center text-sm text-text-muted">
              {t("preview.extractPending")}
            </p>
          ) : (
            <div className="p-4">
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">
                {t("preview.textOf")}
              </p>
              <pre
                data-testid="preview-text"
                className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-text-secondary"
              >
                {text}
              </pre>
            </div>
          )}
        </div>
  );
}

function DownloadButton({ file }: { file: FileAttachment }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={() => void api.downloadFile(file.id, file.filename)}
      aria-label={t("preview.download")}
      className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
    >
      <Download width={14} height={14} aria-hidden />
      <span className="hidden sm:inline">{t("preview.download")}</span>
    </button>
  );
}

/**
 * FIX110 — the one way any card opens a file in place: the docked side pane
 * when the page hosts the slot (the conversation page, on the web and in the
 * desktop shell, which loads the same page), else this modal. Until FIX110
 * only MessageAttachments knew this; the Excel/Word/PowerPoint result cards
 * offered Download alone, for files the same viewer already read when a
 * user attached them.
 */
export function useFilePreviewOpener(): {
  open: (file: FileAttachment) => void;
  /** Render this next to the trigger — the modal fallback, when in use. */
  overlay: ReactNode;
} {
  const hostMounted = useSidePaneStore((s) => s.hostMounted);
  const openPane = useSidePaneStore((s) => s.open);
  const [previewing, setPreviewing] = useState<FileAttachment | null>(null);
  const open = useCallback(
    (f: FileAttachment) => {
      if (hostMounted) openPane({ kind: "preview", file: f });
      else setPreviewing(f);
    },
    [hostMounted, openPane],
  );
  const overlay = previewing ? (
    <FilePreviewModal file={previewing} onClose={() => setPreviewing(null)} />
  ) : null;
  return { open, overlay };
}

/** The centred modal — the FALLBACK frame, used only where no page hosts
 *  the side-pane slot (quick-ask, the public share view). On the
 *  conversation page the preview opens in FilePreviewPane instead. */
export function FilePreviewModal({
  file,
  onClose,
}: {
  file: FileAttachment;
  onClose: () => void;
}) {
  const t = useT();
  // Mounted = open (the parent conditionally renders this modal), so the
  // trap is always active for the component's lifetime.
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);

  // Esc closes, matching every other overlay in the app.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 sm:p-6"
      onClick={onClose}
      data-testid="preview-backdrop"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={file.filename}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">
            {file.filename}
          </span>
          <DownloadButton file={file} />
          <button
            type="button"
            onClick={onClose}
            aria-label={t("preview.close")}
            className="focus-ring rounded p-1 text-text-muted transition-colors hover:text-text-primary"
          >
            <X width={16} height={16} aria-hidden />
          </button>
        </div>
        <FilePreviewBody file={file} />
      </div>
    </div>
  );
}

/** FIX76 — the preview in the page's side-pane slot: docked beside the
 *  thread at ≥ lg (the chat stays live), a slide-over dialog below.
 *  Mounted once by the conversation page; opened by MessageAttachments
 *  through sidePaneStore. */
export function FilePreviewPane() {
  const t = useT();
  const pane = useSidePaneStore((s) => s.pane);
  const closeIf = useSidePaneStore((s) => s.closeIf);
  const file = pane?.kind === "preview" ? pane.file : null;
  // Keep the last file mounted through the overlay's slide-out so the
  // frame does not empty mid-transition.
  const lastFile = useRef<FileAttachment | null>(null);
  if (file) lastFile.current = file;
  const shown = file ?? lastFile.current;
  return (
    <SidePane
      open={file !== null}
      widthKey="preview"
      label={shown?.filename ?? t("preview.close")}
      actions={shown ? <DownloadButton file={shown} /> : null}
      maxWidth={672}
      closeLabel={t("preview.close")}
      onClose={() => closeIf("preview")}
      testId="preview-pane"
    >
      {shown ? <FilePreviewBody key={shown.id} file={shown} /> : null}
    </SidePane>
  );
}
