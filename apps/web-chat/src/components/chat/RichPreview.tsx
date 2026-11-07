"use client";

// Phase 16 — rich renderers for the in-app file preview.
//
// Before this, an .xlsx opened as the extraction pipeline's flat text: a
// tab-separated dump, capped, with "showing the first part only" at the
// bottom. Here a spreadsheet is a spreadsheet — sheet tabs, column
// letters, row numbers — and pressing "load more rows" fetches the next
// window instead of admitting defeat.
//
// SECURITY: every string below comes from a document we did not write.
// All of it is rendered as React text children, which escape, and none
// of these components uses dangerouslySetInnerHTML — a cell containing
// "<script>…" is shown, never run. Keep it that way.

import type {
  DocumentPreview,
  PreviewBlock,
  SheetPreview,
  SlidesPreview,
} from "@/lib/previewData";
import { rowLabel } from "@/lib/previewData";
import { useT } from "@/lib/i18n";

/** Rows/blocks/slides fetched per "load more". */
export const PREVIEW_PAGE_ROWS = 200;
export const PREVIEW_PAGE_BLOCKS = 150;
export const PREVIEW_PAGE_SLIDES = 25;

function LoadMore({
  onClick,
  loading,
  label,
}: {
  onClick: () => void;
  loading: boolean;
  label: string;
}) {
  const t = useT();
  return (
    <div className="flex justify-center border-t border-border p-3">
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        data-testid="preview-load-more"
        className="focus-ring rounded-md border border-border px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
      >
        {loading ? t("preview.loadingMore") : label}
      </button>
    </div>
  );
}

// ── Spreadsheet ────────────────────────────────────────────────────────

export function SheetGrid({
  sheet,
  onSelectSheet,
  onLoadMore,
  loading,
}: {
  sheet: SheetPreview;
  onSelectSheet: (index: number) => void;
  onLoadMore: () => void;
  loading: boolean;
}) {
  const t = useT();
  const shown = sheet.rows.length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {sheet.names.length > 1 && (
        <div
          role="tablist"
          aria-label={t("preview.sheets")}
          className="flex shrink-0 gap-1 overflow-x-auto border-b border-border bg-bg-secondary px-2 py-1.5"
        >
          {sheet.names.map((name, i) => (
            <button
              key={`${name}-${i}`}
              type="button"
              role="tab"
              aria-selected={i === sheet.index}
              onClick={() => onSelectSheet(i)}
              data-testid="preview-sheet-tab"
              className={`focus-ring shrink-0 rounded-md px-2.5 py-1 text-xs transition-colors ${
                i === sheet.index
                  ? "bg-bg-elevated font-medium text-text-primary"
                  : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {/* The grid's own scroll container: a wide sheet must scroll here,
          never push the page sideways. */}
      <div className="min-h-0 flex-1 overflow-auto">
        {shown === 0 ? (
          <p className="p-6 text-center text-sm text-text-muted">
            {t("preview.emptySheet")}
          </p>
        ) : (
          <table
            data-testid="preview-sheet"
            className="border-collapse text-sm"
            style={{ tableLayout: "auto" }}
          >
            <thead className="sticky top-0 z-10">
              <tr>
                <th className="sticky left-0 z-20 border border-border bg-bg-secondary px-2 py-1 text-xs font-normal text-text-muted" />
                {sheet.columns.map((letter) => (
                  <th
                    key={letter}
                    scope="col"
                    className="border border-border bg-bg-secondary px-2 py-1 text-xs font-medium text-text-muted"
                  >
                    {letter}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, i) => (
                <tr key={`${sheet.index}-${sheet.offset + i}`}>
                  <th
                    scope="row"
                    className="sticky left-0 z-10 border border-border bg-bg-secondary px-2 py-1 text-right text-xs font-normal text-text-muted"
                  >
                    {rowLabel(sheet.offset, i)}
                  </th>
                  {sheet.columns.map((_, c) => (
                    <td
                      key={c}
                      className="max-w-xs truncate whitespace-pre border border-border px-2 py-1 text-text-primary"
                      title={row[c] ?? ""}
                    >
                      {row[c] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="shrink-0 border-t border-border px-3 py-1.5 text-xs text-text-muted">
        {t("preview.rowCount", { shown: String(shown), total: String(sheet.total_rows) })}
        {sheet.columns_truncated && ` · ${t("preview.wideSheet")}`}
      </div>

      {sheet.has_more && (
        <LoadMore onClick={onLoadMore} loading={loading} label={t("preview.loadMoreRows")} />
      )}
    </div>
  );
}

// ── Blocks (Word + slide bodies) ───────────────────────────────────────

function BlockTable({ rows }: { rows: string[][] }) {
  return (
    <div className="my-3 overflow-x-auto">
      <table className="border-collapse text-sm">
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td
                  key={c}
                  className={`border border-border px-2 py-1 align-top ${
                    r === 0 ? "bg-bg-secondary font-medium text-text-primary" : "text-text-secondary"
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HEADING_CLASS: Record<number, string> = {
  1: "mt-5 mb-2 text-xl font-semibold",
  2: "mt-4 mb-2 text-lg font-semibold",
  3: "mt-4 mb-1.5 text-base font-semibold",
};

export function Blocks({ blocks }: { blocks: PreviewBlock[] }) {
  return (
    <>
      {blocks.map((block, i) => {
        if (block.type === "table") {
          return <BlockTable key={i} rows={block.rows} />;
        }
        if (block.type === "heading") {
          const level = Math.min(Math.max(block.level, 1), 6);
          return (
            <p
              key={i}
              data-testid={`preview-heading-${level}`}
              // A <p> with heading styling, not an <h1>: this is a
              // preview pane inside a dialog, and injecting a document's
              // own heading levels into the page outline would make the
              // app's landmarks depend on whatever file is open.
              className={`text-text-primary ${HEADING_CLASS[level] ?? "mt-3 mb-1 text-sm font-semibold"}`}
            >
              {block.text}
            </p>
          );
        }
        if (block.type === "list") {
          return (
            <p
              key={i}
              data-testid="preview-list"
              className="my-1 flex gap-2 text-sm leading-relaxed text-text-secondary"
              style={{ paddingLeft: `${(block.level ?? 0) * 16}px` }}
            >
              <span aria-hidden className="select-none text-text-muted">
                •
              </span>
              <span>{block.text}</span>
            </p>
          );
        }
        return (
          <p key={i} className="my-2 whitespace-pre-wrap text-sm leading-relaxed text-text-secondary">
            {block.text}
          </p>
        );
      })}
    </>
  );
}

export function DocumentView({
  doc,
  onLoadMore,
  loading,
}: {
  doc: DocumentPreview;
  onLoadMore: () => void;
  loading: boolean;
}) {
  const t = useT();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-auto px-5 py-3" data-testid="preview-document">
        <Blocks blocks={doc.blocks} />
      </div>
      {doc.has_more && (
        <LoadMore onClick={onLoadMore} loading={loading} label={t("preview.loadMoreBlocks")} />
      )}
    </div>
  );
}

export function SlidesView({
  deck,
  onLoadMore,
  loading,
}: {
  deck: SlidesPreview;
  onLoadMore: () => void;
  loading: boolean;
}) {
  const t = useT();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4" data-testid="preview-slides">
        {deck.slides.map((slide) => (
          <section
            key={slide.index}
            data-testid="preview-slide"
            className="rounded-lg border border-border bg-bg-secondary p-4"
          >
            <p className="mb-2 text-xs uppercase tracking-wide text-text-muted">
              {t("preview.slideN", { n: String(slide.index), total: String(deck.total_slides) })}
            </p>
            {slide.title && (
              <p className="mb-2 text-base font-semibold text-text-primary">{slide.title}</p>
            )}
            {slide.blocks.length === 0 && !slide.title ? (
              <p className="text-sm text-text-muted">{t("preview.emptySlide")}</p>
            ) : (
              <Blocks blocks={slide.blocks} />
            )}
          </section>
        ))}
      </div>
      {deck.has_more && (
        <LoadMore onClick={onLoadMore} loading={loading} label={t("preview.loadMoreSlides")} />
      )}
    </div>
  );
}
