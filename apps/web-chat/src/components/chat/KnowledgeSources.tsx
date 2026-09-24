"use client";

// FIX132 — what a knowledge-base answer drew on, the way ChatGPT and Claude
// Projects show it:
//   * a `[Source: file]` marker in the answer is a pill; clicking it opens a
//     small popover (not a modal) with the file's name, the passage(s) the
//     model was given from it, and "เปิดไฟล์ต้นฉบับ";
//   * under the answer, "แหล่งข้อมูล" lists every document used — shown even
//     when the model forgot to write a marker;
//   * when files were still being processed at answer time, one quiet line
//     says the answer used only the files that were ready.
// All of it reads the message's context_sources (knowledge_chunk /
// knowledge_pending, CONTRACT.md §Messages). Everything is <span>-built: a
// pill sits inside a paragraph, where a <div> is not allowed.

import { useCallback, useMemo, useRef, useState } from "react";
import { Clock, FileText } from "lucide-react";

import { useDismissable } from "@/hooks/useDismissable";
import { useOpenKnowledgeFile } from "@/hooks/useOpenKnowledgeFile";
import { useT } from "@/lib/i18n";
import {
  findCitedDoc,
  knowledgeDocsOf,
  knowledgePendingOf,
  type KnowledgeCitedDoc,
} from "@/lib/knowledge";
import type { Message } from "@/lib/types";

import { CitationRendererContext, type CitationRenderer } from "./Markdown";

const PILL =
  "focus-ring mx-0.5 inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-border bg-bg-elevated px-2 py-0.5 align-baseline text-[11px] font-medium text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary";

const MAX_EXCERPTS = 3;

/** Gives the answer's `[Source: …]` pills their popover. A marker that names
 *  no document the answer used keeps the plain pill. */
export function KnowledgeCitationScope({
  message,
  children,
}: {
  message: Pick<Message, "context_sources">;
  children: React.ReactNode;
}) {
  const docs = useMemo(() => knowledgeDocsOf(message.context_sources), [message.context_sources]);
  const render = useMemo<CitationRenderer | null>(() => {
    if (docs.length === 0) return null;
    return (filename: string) => {
      const doc = findCitedDoc(docs, filename);
      return doc ? <KnowledgeSourceButton doc={doc} label={filename} testId="kb-citation" /> : null;
    };
  }, [docs]);
  if (!render) return <>{children}</>;
  return <CitationRendererContext.Provider value={render}>{children}</CitationRendererContext.Provider>;
}

/** "แหล่งข้อมูล" under the answer: one chip per document used. */
export function KnowledgeSourcesRow({ message }: { message: Pick<Message, "context_sources"> }) {
  const t = useT();
  const docs = useMemo(() => knowledgeDocsOf(message.context_sources), [message.context_sources]);
  if (docs.length === 0) return null;
  return (
    <div data-testid="kb-sources" className="mt-2 flex flex-wrap items-center gap-1">
      <span className="mr-0.5 text-[11px] font-medium text-text-muted">{t("kb.sources.title")}</span>
      {docs.map((d) => (
        <KnowledgeSourceButton key={d.documentId || d.filename} doc={d} label={d.filename} testId="kb-source-chip" />
      ))}
    </div>
  );
}

/** The files the answer could not use yet (still being processed). */
export function KnowledgePendingNotice({ message }: { message: Pick<Message, "context_sources"> }) {
  const t = useT();
  const pending = knowledgePendingOf(message.context_sources);
  if (!pending) return null;
  return (
    <p
      data-testid="kb-pending-notice"
      title={pending.names.join("\n") || undefined}
      className="mt-1 flex items-center gap-1.5 text-[11px] text-text-muted"
    >
      <Clock className="h-3 w-3 shrink-0" aria-hidden />
      <span>{t("kb.pending.notice", { n: String(pending.count) })}</span>
    </p>
  );
}

function KnowledgeSourceButton({
  doc,
  label,
  testId,
}: {
  doc: KnowledgeCitedDoc;
  label: string;
  testId: string;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismissable(wrapRef, open, close);
  return (
    <span ref={wrapRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={t("kb.cite.from", { name: doc.filename })}
        data-testid={testId}
        className={PILL}
      >
        <FileText className="h-2.5 w-2.5 shrink-0" aria-hidden />
        <span className="truncate">{label}</span>
      </button>
      {open ? <KnowledgeSourcePopover doc={doc} /> : null}
    </span>
  );
}

function KnowledgeSourcePopover({ doc }: { doc: KnowledgeCitedDoc }) {
  const t = useT();
  const { open, busy, error } = useOpenKnowledgeFile();
  const canOpen = Boolean(doc.kbId && doc.documentId);
  const excerpts = doc.excerpts.slice(0, MAX_EXCERPTS);
  return (
    <span
      role="dialog"
      aria-label={doc.filename}
      data-testid="kb-cite-popover"
      className="popover-appear absolute left-0 top-full z-30 mt-1 block w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border bg-bg-surface text-left font-normal shadow-2xl"
    >
      <span className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <FileText className="h-3.5 w-3.5 shrink-0 text-text-muted" aria-hidden />
        <span className="truncate text-xs font-semibold text-text-primary">{doc.filename}</span>
      </span>
      <span className="scrollbar-thin block max-h-56 overflow-y-auto px-3 py-2">
        <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">
          {t("kb.cite.excerpts")}
        </span>
        {excerpts.length > 0 ? (
          excerpts.map((e, i) => (
            <span
              key={i}
              data-testid="kb-cite-excerpt"
              className="mb-2 block whitespace-pre-line border-l-2 border-border-strong pl-2 text-xs leading-relaxed text-text-secondary"
            >
              {e.text}
            </span>
          ))
        ) : (
          <span className="block text-xs text-text-muted">{t("kb.cite.noExcerpt")}</span>
        )}
      </span>
      <span className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-3 py-2">
        {canOpen ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void open(doc.kbId, doc.documentId, doc.filename)}
            className="focus-ring rounded-md border border-border bg-transparent px-2.5 py-1 text-xs font-medium text-text-primary transition-colors hover:bg-bg-elevated disabled:opacity-60"
          >
            {busy ? t("kb.open.opening") : t("kb.cite.openOriginal")}
          </button>
        ) : null}
        {error ? (
          <span role="alert" className="text-[11px] text-status-danger">
            {t(error)}
          </span>
        ) : null}
      </span>
    </span>
  );
}
