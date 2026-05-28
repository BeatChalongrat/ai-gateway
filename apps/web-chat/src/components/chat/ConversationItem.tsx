"use client";

import { useEffect, useRef, useState } from "react";

import { BrainCircuit } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { Conversation } from "@/lib/types";

interface Props {
  conv: Conversation;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void | Promise<void>;
  onDelete: () => void | Promise<void>;
}

export function ConversationItem({
  conv,
  active,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const t = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState(conv.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(conv.title);
      inputRef.current?.select();
    }
  }, [renaming, conv.title]);

  useEffect(() => {
    if (!menuOpen) return;
    const off = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [menuOpen]);

  return (
    <div
      className={`group relative flex flex-col rounded-lg text-[13px] transition-colors ${
        active
          ? "bg-bg-elevated text-text-primary"
          : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
      }`}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={() => {
          if (renaming || confirmingDelete) return;
          onSelect();
        }}
        onKeyDown={(e) => {
          if (renaming || confirmingDelete) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect();
          }
        }}
        className="flex items-center gap-2 px-3 py-1.5"
      >
        {renaming ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              const t = draft.trim();
              if (t && t !== conv.title) void onRename(t);
              setRenaming(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.currentTarget.blur();
              } else if (e.key === "Escape") {
                setDraft(conv.title);
                setRenaming(false);
              }
            }}
            className="w-full rounded bg-bg-base px-1.5 py-0.5 text-sm text-text-primary outline-none focus:ring-1 focus:ring-border-strong"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span
            className="flex flex-1 items-center gap-1.5 truncate"
            title={
              conv.memory_enabled === false
                ? `${conv.title || "Untitled"} — temporary chat (memory off)`
                : conv.title
            }
          >
            {conv.memory_enabled === false ? (
              <BrainCircuit
                width={12}
                height={12}
                aria-hidden
                className="shrink-0 text-text-muted opacity-70"
              />
            ) : null}
            <span className="truncate">{conv.title || "Untitled"}</span>
          </span>
        )}
        <button
          type="button"
          aria-label="Conversation actions"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          className={`focus-ring rounded p-1 text-text-muted transition-opacity hover:bg-bg-secondary hover:text-text-primary md:opacity-0 md:group-hover:opacity-100 ${
            menuOpen || active ? "md:opacity-100" : ""
          }`}
        >
          <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden>
            <circle cx="3" cy="8" r="1.5" fill="currentColor" />
            <circle cx="8" cy="8" r="1.5" fill="currentColor" />
            <circle cx="13" cy="8" r="1.5" fill="currentColor" />
          </svg>
        </button>
        {menuOpen ? (
          <div
            ref={menuRef}
            className="popover-appear absolute right-1 top-9 z-10 w-32 overflow-hidden rounded-md border border-border bg-bg-surface text-xs shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                setRenaming(true);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M11.5 1.5l3 3L5 14H2v-3L11.5 1.5z" />
              </svg>
              {t("conv.rename")}
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                setConfirmingDelete(true);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-status-danger hover:bg-status-danger/15"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M2 4h12M5.333 4V2.667a1.333 1.333 0 011.334-1.334h2.666a1.333 1.333 0 011.334 1.334V4M6.667 7.333v4M9.333 7.333v4" />
                <path d="M3.333 4l.667 9.333a1.333 1.333 0 001.333 1.334h5.334a1.333 1.333 0 001.333-1.334L12.667 4" />
              </svg>
              {t("conv.delete")}
            </button>
          </div>
        ) : null}
      </div>

      {confirmingDelete ? (
        <div
          className="border-t border-border/60 px-3 py-2 text-[11px]"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="text-text-secondary">
            Delete this conversation? This cannot be undone.
          </p>
          <div className="mt-1.5 flex gap-2">
            <button
              type="button"
              onClick={() => {
                setConfirmingDelete(false);
                void onDelete();
              }}
              className="focus-ring rounded bg-status-danger/85 px-2 py-1 font-medium text-white hover:bg-status-danger"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="focus-ring rounded border border-border px-2 py-1 text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
