"use client";

// KBSelector — a small pill+dropdown next to the model picker that
// binds (or unbinds) a knowledge base to the current conversation.
//
// Rendering rules:
//   • Pill is fully transparent (border-only) when no KB is bound.
//   • Pill flips to the accent tint when a KB is active so the user
//     can tell at a glance the AI is retrieving context.
//   • Dropdown lists every KB the tenant has + a "None" option to
//     unbind. The currently-bound KB carries a checkmark.
//
// Lazy-loads the KB list on first dropdown open so the chat page
// doesn't pay the round-trip until the user actually cares.

import { useEffect, useRef, useState } from "react";

import { useKnowledgeStore } from "@/stores/knowledgeStore";

interface Props {
  /** Conversation id is required; the selector is hidden when there
   *  isn't an active conversation (the landing page can't bind). */
  conversationId: string | null;
  /** Currently-bound KB id (null when unbound). */
  activeKBId: string | null;
  /** Async — caller wires this to chatStore.setConversationKB. */
  onPick: (kbId: string | null) => Promise<void>;
  disabled?: boolean;
}

export function KBSelector({
  conversationId,
  activeKBId,
  onPick,
  disabled,
}: Props) {
  const knowledgeBases = useKnowledgeStore((s) => s.knowledgeBases);
  const loaded = useKnowledgeStore((s) => s.loaded);
  const loading = useKnowledgeStore((s) => s.loading);
  const loadKBs = useKnowledgeStore((s) => s.loadKnowledgeBases);

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click. We use mousedown so clicking inside still
  // gets a chance to handle the event before the outer guard fires.
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  // Lazy-load on first open so the chat page doesn't pay the round-
  // trip for users who never use RAG.
  useEffect(() => {
    if (open && !loaded && !loading) void loadKBs();
  }, [open, loaded, loading, loadKBs]);

  if (!conversationId) return null;

  const activeKB = activeKBId
    ? knowledgeBases.find((k) => k.id === activeKBId)
    : null;
  const isActive = !!activeKB;

  const label = activeKB ? activeKB.name : "Knowledge";

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-label={
          activeKB ? `Knowledge base: ${activeKB.name}` : "Pick a knowledge base"
        }
        title={
          activeKB ? `Knowledge base: ${activeKB.name}` : "Pick a knowledge base"
        }
        className={`focus-ring inline-flex h-8 max-w-[140px] items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors disabled:opacity-50 ${
          isActive
            ? "border-accent/40 bg-accent/10 text-accent"
            : "border-border bg-transparent text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
        }`}
      >
        <BookIcon />
        <span className="truncate">{label}</span>
      </button>
      {open ? (
        <div className="popover-appear absolute bottom-10 right-0 z-30 w-64 overflow-hidden rounded-lg border border-border bg-bg-surface text-xs shadow-2xl">
          <div className="border-b border-border/60 px-3 py-2">
            <p className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
              Knowledge base
            </p>
          </div>
          <div className="max-h-72 overflow-y-auto">
            <KBRow
              checked={!activeKBId}
              onClick={async () => {
                setOpen(false);
                if (activeKBId) await onPick(null);
              }}
              title="None"
              subtitle="No retrieval — chat with the model directly"
            />
            {loading && !loaded ? (
              <div className="px-3 py-3 text-text-muted">Loading…</div>
            ) : knowledgeBases.length === 0 ? (
              <div className="px-3 py-3 text-text-muted">
                No knowledge bases yet.{" "}
                <a
                  href="/chat/knowledge"
                  className="text-accent hover:underline"
                >
                  Create one
                </a>
                .
              </div>
            ) : (
              knowledgeBases.map((kb) => (
                <KBRow
                  key={kb.id}
                  checked={activeKBId === kb.id}
                  onClick={async () => {
                    setOpen(false);
                    if (activeKBId !== kb.id) await onPick(kb.id);
                  }}
                  title={kb.name}
                  subtitle={`${kb.document_count} ${kb.document_count === 1 ? "doc" : "docs"} · ${kb.total_chunks} ${kb.total_chunks === 1 ? "chunk" : "chunks"}`}
                />
              ))
            )}
          </div>
          <div className="border-t border-border/60 px-3 py-2">
            <a
              href="/chat/knowledge"
              className="text-[11px] text-text-muted hover:text-text-primary"
            >
              Manage knowledge bases →
            </a>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function KBRow({
  checked,
  title,
  subtitle,
  onClick,
}: {
  checked: boolean;
  title: string;
  subtitle: string;
  onClick: () => void | Promise<void>;
}) {
  return (
    <button
      type="button"
      onClick={() => void onClick()}
      className="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-elevated"
    >
      <span className="mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center text-accent">
        {checked ? <CheckIcon /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-text-primary">{title}</span>
        <span className="block truncate text-[10px] text-text-muted">
          {subtitle}
        </span>
      </span>
    </button>
  );
}

function BookIcon() {
  return (
    <svg
      width={12}
      height={12}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width={11}
      height={11}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
