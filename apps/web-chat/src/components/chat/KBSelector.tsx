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
// doesn't pay the round-trip until the user actually cares — unless a KB is
// attached, whose name the pill must show (FIX132).
//
// FIX132 — the pill is a chip: it names the attached KB without opening the
// dropdown, and its × takes it off this chat in one click (below `sm` the ×
// is hidden for room, like the label; the dropdown's "ไม่ใช้" still removes).
// A chat inside a project whose KB the server uses (conversation KB ?? the
// project's) shows that KB too, marked as the project's; it is changed on
// the project page, so it has no ×.
//
// AM6 — an agent's workspace has no conversation and keeps the choice
// itself (per agent, for questions only): it asks for the chip as it is with
// `standalone`. The chat never passes it.

import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { useDismissable } from "@/hooks/useDismissable";
import { useT } from "@/lib/i18n";

import { useChatStore } from "@/stores/chatStore";
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
  /** AM6 — shown without a conversation: the caller keeps the choice. */
  standalone?: boolean;
}

export function KBSelector({
  conversationId,
  activeKBId,

  onPick,
  disabled,
  standalone = false,
}: Props) {
  const t = useT();
  const knowledgeBases = useKnowledgeStore((s) => s.knowledgeBases);
  const loaded = useKnowledgeStore((s) => s.loaded);
  const loading = useKnowledgeStore((s) => s.loading);
  const loadKBs = useKnowledgeStore((s) => s.loadKnowledgeBases);

  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // FIX84 row 3 — outside-click AND Escape, through the one shared rule.
  const closePopover = useCallback(() => setOpen(false), []);
  useDismissable(wrapRef, open, closePopover);

  // The project's KB, which the server uses when the chat has none of its own.
  const inheritedKBId = useChatStore((s) => {
    if (!conversationId) return null;
    const projectId = s.conversations.find((c) => c.id === conversationId)?.project_id;
    if (!projectId) return null;
    return s.projects.find((p) => p.id === projectId)?.knowledge_base_id ?? null;
  });
  const shownKBId = activeKBId ?? inheritedKBId;

  // Lazy-load on first open so the chat page doesn't pay the round-
  // trip for users who never use RAG; an attached KB needs its name now.
  useEffect(() => {
    if ((open || shownKBId) && !loaded && !loading) void loadKBs();
  }, [open, shownKBId, loaded, loading, loadKBs]);

  if (!conversationId && !standalone) return null;

  const activeKB = activeKBId
    ? knowledgeBases.find((k) => k.id === activeKBId)
    : null;
  const inheritedKB = !activeKBId && inheritedKBId
    ? knowledgeBases.find((k) => k.id === inheritedKBId)
    : null;
  const isActive = !!activeKBId || !!inheritedKB;

  const label = activeKB?.name ?? inheritedKB?.name ?? t("kb.picker.label");
  const pickLabel = activeKB
    ? t("kb.picker.active", { name: activeKB.name })
    : inheritedKB
      ? t("kb.chip.fromProject", { name: inheritedKB.name })
      : activeKBId
        ? t("kb.picker.label")
        : t("kb.picker.pick");

  return (
    <div ref={wrapRef} className="relative">
      <div
        className={`inline-flex h-8 max-w-[180px] items-center rounded-full border text-xs transition-colors ${
          isActive
            ? "border-border-strong bg-bg-elevated text-text-primary"
            : "border-border bg-transparent text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
        }`}
      >
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={disabled}
          aria-label={pickLabel}
          title={pickLabel}
          className="focus-ring inline-flex h-full min-w-0 items-center gap-1.5 rounded-full px-2.5 disabled:opacity-50"
        >
          <BookIcon />
          {/* FIX128 D4 — icon-only below sm: at 360 px the label pushed the
              send button out of the composer. The name stays in aria-label/title. */}
          <span className="hidden truncate sm:inline">{label}</span>
        </button>
        {activeKBId ? (
          <button
            type="button"
            onClick={() => void onPick(null)}
            disabled={disabled}
            aria-label={t("kb.chip.remove")}
            title={t("kb.chip.remove")}
            className="focus-ring -ml-1 mr-1 hidden h-5 w-5 shrink-0 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-bg-base hover:text-text-primary disabled:opacity-50 sm:inline-flex"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="popover-appear absolute bottom-10 right-0 z-30 w-64 overflow-hidden rounded-lg border border-border bg-bg-surface text-xs shadow-2xl">
          <div className="border-b border-border px-3 py-2">
            <p className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
              {t("menu.knowledgeBase")}
            </p>
          </div>
          {inheritedKB ? (
            <p className="border-b border-border px-3 py-2 text-[11px] text-text-muted">
              {t("kb.picker.projectNote", { name: inheritedKB.name })}
            </p>
          ) : null}
          <div className="max-h-72 overflow-y-auto">
            <KBRow
              checked={!activeKBId}
              onClick={async () => {
                setOpen(false);
                if (activeKBId) await onPick(null);
              }}
              title={t("kb.picker.none")}
              subtitle={t("kb.picker.noneHint")}
            />
            {loading && !loaded ? (
              <div className="px-3 py-3 text-text-muted">{t("common.loading")}</div>
            ) : knowledgeBases.length === 0 ? (
              <div className="px-3 py-3 text-text-muted">
                {t("kb.empty.title")}{" "}
                <a
                  href="/chat/knowledge"
                  className="text-text-primary underline-offset-2 hover:underline"
                >
                  {t("kb.picker.createOne")}
                </a>
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
                  subtitle={`${kb.document_count} ${kb.document_count === 1 ? t("kb.unit.document") : t("kb.unit.documents")} · ${kb.total_chunks} ${kb.total_chunks === 1 ? t("kb.unit.chunk") : t("kb.unit.chunks")}`}
                />
              ))
            )}
          </div>
          <div className="border-t border-border px-3 py-2">
            <a
              href="/chat/knowledge"
              className="text-[11px] text-text-muted hover:text-text-primary"
            >
              {t("kb.picker.manage")}
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
      <span className="mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center text-text-primary">
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
