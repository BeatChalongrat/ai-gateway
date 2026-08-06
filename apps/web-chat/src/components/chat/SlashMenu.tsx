"use client";

// Phase 10 Task 2 — the slash quick-action menu.
//
// Floats above the composer while the input is a bare slash token (see
// lib/slashCommands.ts for the trigger rule). Keyboard-first: ↑/↓ move,
// Enter/Tab run, Esc dismisses — all handled by the composer, which owns
// the textarea's key events; this component only renders and reports
// clicks, so the two can never fight over focus.

import { ImageIcon, Keyboard, MessageSquarePlus, Video } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { SlashActionId, SlashCommand } from "@/lib/slashCommands";

const ICONS: Record<SlashActionId, React.ReactNode> = {
  image: <ImageIcon width={15} height={15} aria-hidden />,
  video: <Video width={15} height={15} aria-hidden />,
  new: <MessageSquarePlus width={15} height={15} aria-hidden />,
  shortcuts: <Keyboard width={15} height={15} aria-hidden />,
};

export function SlashMenu({
  commands,
  activeIndex,
  onPick,
}: {
  commands: SlashCommand[];
  activeIndex: number;
  onPick: (cmd: SlashCommand) => void;
}) {
  const t = useT();

  return (
    <div
      data-testid="slash-menu"
      role="listbox"
      aria-label={t("slash.title")}
      className="absolute bottom-full left-0 z-30 mb-2 w-72 overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-xl"
    >
      {commands.length === 0 ? (
        <p className="px-3 py-2.5 text-xs text-text-muted">{t("slash.empty")}</p>
      ) : (
        commands.map((cmd, i) => (
          <button
            key={cmd.id}
            type="button"
            role="option"
            aria-selected={i === activeIndex}
            // onMouseDown, not onClick: the textarea must not lose focus
            // before the action runs, or the composer's caret jumps.
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(cmd);
            }}
            className={`flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors ${
              i === activeIndex
                ? "bg-bg-elevated text-text-primary"
                : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
            }`}
          >
            <span className="shrink-0 text-text-muted">{ICONS[cmd.id]}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm">{t(cmd.labelKey)}</span>
              <span className="block truncate text-[11px] text-text-muted">
                {t(cmd.hintKey)}
              </span>
            </span>
            <kbd className="shrink-0 rounded border border-border bg-bg-base px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
              /{cmd.keyword}
            </kbd>
          </button>
        ))
      )}
    </div>
  );
}
