"use client";

// T4 — Thinking Mode toggle.
//
// Compact pill that sits next to the ModelSelector in the composer
// chrome. Two interactions:
//
//   1. Click the icon → flips ``reasoning_enabled`` on the
//      conversation (PATCH /conversations/:id). Optimistic with
//      rollback per the chatStore action.
//   2. Click again while enabled (or click the chevron) → opens a
//      tiny popover with the four non-off modes (auto / low /
//      medium / high). Picking one PATCHes ``reasoning_mode``.
//
// Rendered only when the active model advertises reasoning support
// (ModelEntry.reasoning?.supported === true). Auto mode is treated
// as reasoning-capable when ANY model in the catalog is — the
// composer is conservative and asks the server.

import { useEffect, useRef, useState } from "react";

import { BrainCircuit, Check } from "lucide-react";

import { useT } from "@/lib/i18n";

type ReasoningMode = "off" | "auto" | "low" | "medium" | "high";

interface ReasoningToggleProps {
  enabled: boolean;
  mode: ReasoningMode;
  onToggle: (enabled: boolean) => void;
  onModeChange: (mode: ReasoningMode) => void;
  disabled?: boolean;
}

const PICKABLE_MODES: ReasoningMode[] = ["auto", "low", "medium", "high"];

export function ReasoningToggle({
  enabled,
  mode,
  onToggle,
  onModeChange,
  disabled,
}: ReasoningToggleProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close the mode popover on outside-click. Same pattern the
  // ModelSelector uses.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const onIconClick = () => {
    if (disabled) return;
    if (!enabled) {
      onToggle(true);
      // Don't open the popover on the same click that enabled —
      // requires a second tap. Mirrors how the kb chip behaves.
      return;
    }
    setOpen((v) => !v);
  };

  return (
    <div ref={wrapRef} className="relative inline-flex items-center">
      <button
        type="button"
        onClick={onIconClick}
        disabled={disabled}
        aria-label={t("reasoning.toggle")}
        aria-pressed={enabled}
        title={t("reasoning.toggle")}
        className={[
          "focus-ring inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs font-medium transition-colors",
          enabled
            ? "bg-blue-500/15 text-blue-400 hover:bg-blue-500/20"
            : "text-text-tertiary hover:bg-bg-elevated hover:text-text-secondary",
          disabled ? "cursor-not-allowed opacity-50" : "",
        ].join(" ")}
      >
        <BrainCircuit className="h-3.5 w-3.5" aria-hidden />
        {enabled ? <span>{t("reasoning.label")}</span> : null}
      </button>
      {open && enabled ? (
        <div
          role="menu"
          className="absolute bottom-full right-0 z-20 mb-2 min-w-[160px] rounded-lg border border-border-default bg-bg-surface p-1.5 shadow-lg"
        >
          <div className="px-2 py-1 text-[11px] uppercase tracking-wide text-text-muted">
            {t("reasoning.effort")}
          </div>
          {PICKABLE_MODES.map((m) => {
            const active = m === mode;
            return (
              <button
                key={m}
                type="button"
                onClick={() => {
                  onModeChange(m);
                  setOpen(false);
                }}
                className={[
                  "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-xs transition-colors",
                  active
                    ? "bg-surface-secondary text-text-primary"
                    : "text-text-secondary hover:bg-surface-secondary",
                ].join(" ")}
                role="menuitemradio"
                aria-checked={active}
              >
                <span>{t(`reasoning.mode.${m}` as never)}</span>
                {active ? <Check className="h-3 w-3" aria-hidden /> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
