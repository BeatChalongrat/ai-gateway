"use client";

import { useEffect, useRef, useState } from "react";

import { useModelStore } from "@/stores/modelStore";

import { AssistantAvatar } from "./AssistantAvatar";

interface Props {
  /** When set, the dropdown is locked to this model id (per-conversation lock). */
  pinnedTo?: string | null;
  disabled?: boolean;
  /** Compact pill mode used inside ChatInput. */
  compact?: boolean;
}

export function ModelSelector({ pinnedTo, disabled, compact }: Props) {
  const models = useModelStore((s) => s.models);
  const selectedId = useModelStore((s) => s.selectedModelId);
  const selectModel = useModelStore((s) => s.selectModel);

  const activeId = pinnedTo ?? selectedId;
  const activeModel = models.find((m) => m.id === activeId);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  const shortName = shortLabel(activeModel?.name ?? activeModel?.id ?? "");
  const locked = Boolean(pinnedTo);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled || locked}
        onClick={() => setOpen((v) => !v)}
        className={`focus-ring flex items-center gap-1.5 rounded-full border border-border text-xs text-text-secondary transition-all hover:bg-bg-elevated hover:text-text-primary disabled:cursor-default disabled:opacity-80 ${
          compact ? "bg-bg-base/40 px-2 py-1" : "bg-bg-secondary px-2.5 py-1.5"
        }`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={locked ? "Model is locked to this conversation" : "Switch model"}
      >
        {compact ? <AssistantAvatar modelId={activeId ?? null} size={16} /> : null}
        <span className="font-medium text-text-primary">
          {activeModel ? shortName : "Select model"}
        </span>
        {!locked ? (
          <svg
            width={10}
            height={10}
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden
            className="text-text-muted"
          >
            <path
              d="M6 9l6 6 6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : null}
      </button>
      {open && !locked ? (
        <ul
          role="listbox"
          className="popover-appear absolute bottom-full left-0 z-30 mb-2 w-64 overflow-hidden rounded-lg border border-border bg-bg-surface text-xs shadow-2xl"
        >
          {models.map((m) => (
            <li key={m.id} role="option" aria-selected={m.id === activeId}>
              <button
                type="button"
                onClick={() => {
                  selectModel(m.id);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-elevated ${
                  m.id === activeId ? "bg-bg-elevated/80" : ""
                }`}
              >
                <AssistantAvatar modelId={m.id} size={22} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-text-primary">
                    {m.name}
                  </div>
                  <div className="text-[10px] uppercase tracking-wide text-text-muted">
                    {m.provider}
                  </div>
                </div>
                {m.id === activeId ? (
                  <svg
                    width={12}
                    height={12}
                    viewBox="0 0 24 24"
                    fill="none"
                    className="shrink-0 text-accent"
                    aria-hidden
                  >
                    <path
                      d="M5 13l4 4L19 7"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function shortLabel(name: string): string {
  // ChatGPT-style short names: trim verbose model identifiers when
  // rendered inside the compact badge.
  return name.replace(/^anthropic\//, "").replace(/^openai\//, "").trim();
}
