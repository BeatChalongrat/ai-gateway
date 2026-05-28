"use client";

// Minimal model selector — Claude-style.
//
// Trigger pill: model name + chevron, no provider icon, no badge.
// When the user has Auto selected the pill reads "Auto" with a
// Sparkles glyph; the backend receives the literal "auto" sentinel
// and runs its own per-request resolution.
//
// Dropdown: one row per model (name on the left, context size muted
// on the right, checkmark when active). "Auto" sits at the top with
// an i18n-aware description ("Selects the best model automatically").

import { KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { useT } from "@/lib/i18n";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";
import type { ModelEntry } from "@/lib/types";

interface Props {
  /** Model id displayed in the trigger pill. When omitted, falls back
   *  to modelStore.selectedModelId. Conversation pages pass the
   *  conversation's model_id so the pill reflects the conv's binding
   *  rather than the global default. The literal value "auto" is
   *  treated as the Auto sentinel. */
  activeModelId?: string | null;
  disabled?: boolean;
  /** Compact pill mode used inside ChatInput. */
  compact?: boolean;
  /** Called when the user picks a model. The model entry passed in
   *  for the Auto row carries `id === "auto"` — the parent forwards
   *  that string to the backend verbatim. Optional — defaults to
   *  ``modelStore.selectModel`` / ``selectAuto``. */
  onPick?: (model: ModelEntry) => void;
}

// Synthetic ModelEntry used to fire onPick when the user clicks
// Auto. The id is the same sentinel the backend special-cases —
// the rest of the fields are filler so the existing onPick contract
// keeps its non-null shape.
const AUTO_PICK_ENTRY: ModelEntry = {
  id: AUTO_MODEL_ID,
  name: "Auto",
  provider: "auto",
  description: "",
  context_window: 0,
  capabilities: [],
  icon: "",
  category: "fast",
  allowed: true,
};

export function ModelSelector({ activeModelId, disabled, compact, onPick }: Props) {
  const t = useT();
  const models = useModelStore((s) => s.models);
  const selectedId = useModelStore((s) => s.selectedModelId);
  const selectModel = useModelStore((s) => s.selectModel);
  const selectAuto = useModelStore((s) => s.selectAuto);
  const modelsLoaded = useModelStore((s) => s.loaded);
  const modelsLoading = useModelStore((s) => s.loading);

  // Active id resolution: prefer the explicit prop (conversation
  // binding), fall back to the persisted global default.
  const activeId = activeModelId ?? selectedId;
  const isAuto = activeId === AUTO_MODEL_ID;
  const activeModel = isAuto ? null : models.find((m) => m.id === activeId);

  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  // Flat list — Auto first, then every available model in catalog
  // order. We deliberately drop the category grouping and recents
  // section; the catalog is small enough that any extra chrome
  // outweighs the navigation benefit.
  const flat = useMemo<Array<{ id: string; entry: ModelEntry | null }>>(() => {
    return [
      { id: AUTO_MODEL_ID, entry: null },
      ...models.map((m) => ({ id: m.id, entry: m })),
    ];
  }, [models]);

  useEffect(() => {
    if (!open) return;
    const off = (e: globalThis.MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  useEffect(() => {
    setHighlight((p) => (flat.length === 0 ? 0 : Math.min(p, flat.length - 1)));
  }, [flat.length]);

  useEffect(() => {
    if (!open) return;
    setHighlight(0);
    const s = useModelStore.getState();
    if (!s.loaded && !s.loading) void s.loadModels();
  }, [open]);

  const pickAuto = useCallback(() => {
    if (onPick) {
      onPick(AUTO_PICK_ENTRY);
    } else {
      selectAuto();
    }
    setOpen(false);
  }, [onPick, selectAuto]);

  const pick = useCallback(
    (m: ModelEntry) => {
      if (onPick) onPick(m);
      else selectModel(m.id);
      setOpen(false);
    },
    [onPick, selectModel],
  );

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((p) => (flat.length ? (p + 1) % flat.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((p) => (flat.length ? (p - 1 + flat.length) % flat.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = flat[highlight];
      if (!row) return;
      if (row.id === AUTO_MODEL_ID) pickAuto();
      else if (row.entry) pick(row.entry);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  }

  const triggerLabel = isAuto
    ? t("model.auto.name")
    : activeModel
      ? shortLabel(activeModel.name ?? activeModel.id)
      : t("model.selectModel");

  return (
    <div ref={ref} className="relative" onKeyDown={handleKeyDown}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`focus-ring flex items-center gap-1 rounded-full text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-default disabled:opacity-80 ${
          compact ? "px-2 py-1" : "px-2.5 py-1.5"
        }`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={t("model.selectModel")}
      >
        {isAuto ? (
          <Sparkles width={12} height={12} className="text-text-tertiary" aria-hidden />
        ) : null}
        <span className="font-medium text-text-primary">{triggerLabel}</span>
        <svg width={10} height={10} viewBox="0 0 24 24" fill="none" aria-hidden className="text-text-muted">
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open ? (
        <>
          {/* Mobile-only backdrop — the dropdown becomes a bottom sheet
              below 640px, so we dim the layer behind it. */}
          <div
            aria-hidden
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 bg-black/55 backdrop-blur-sm sm:hidden"
          />
          <div
            role="listbox"
            aria-label="Models"
            className="
              popover-appear sheet-up z-40 flex flex-col overflow-hidden
              border border-border bg-bg-surface text-xs shadow-2xl
              fixed inset-x-0 bottom-0 max-h-[70vh] rounded-t-2xl safe-bottom
              sm:absolute sm:bottom-full sm:left-0 sm:inset-x-auto sm:mb-2
              sm:w-[min(280px,90vw)] sm:max-h-[360px] sm:rounded-xl
              sm:sheet-up:animate-none
            "
          >
            <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>

            <div className="scrollbar-thin flex-1 overflow-y-auto py-1">
              {flat.length <= 1 ? (
                <div className="px-3 py-6 text-center text-text-muted">
                  {modelsLoading || !modelsLoaded ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
                      Loading…
                    </span>
                  ) : (
                    <>No models available.</>
                  )}
                </div>
              ) : null}

              {flat.map((row, idx) => {
                if (row.id === AUTO_MODEL_ID) {
                  return (
                    <Row
                      key="auto"
                      label={t("model.auto.name")}
                      sub={t("model.auto.description")}
                      selected={isAuto}
                      highlighted={highlight === idx}
                      onClick={pickAuto}
                      onHover={() => setHighlight(idx)}
                      leading={<Sparkles width={13} height={13} className="text-text-tertiary" aria-hidden />}
                    />
                  );
                }
                const m = row.entry!;
                const isSelected = !isAuto && m.id === activeId;
                return (
                  <Row
                    key={m.id}
                    label={m.name}
                    sub={m.context_window > 0 ? formatContext(m.context_window) : undefined}
                    selected={isSelected}
                    highlighted={highlight === idx}
                    onClick={() => pick(m)}
                    onHover={() => setHighlight(idx)}
                  />
                );
              })}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

interface RowProps {
  label: string;
  sub?: string;
  selected: boolean;
  highlighted: boolean;
  onClick: () => void;
  onHover: () => void;
  leading?: React.ReactNode;
}

function Row({ label, sub, selected, highlighted, onClick, onHover, leading }: RowProps) {
  return (
    <div
      role="option"
      aria-selected={selected}
      onClick={onClick}
      onMouseEnter={onHover}
      className={`flex cursor-pointer items-center gap-2 px-3 py-2 transition-colors ${
        highlighted ? "bg-bg-elevated" : selected ? "bg-bg-elevated/60" : "hover:bg-bg-elevated/60"
      }`}
    >
      {leading ? <span className="inline-flex shrink-0 items-center">{leading}</span> : null}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-text-primary">{label}</div>
        {sub ? <div className="truncate text-[11px] text-text-muted">{sub}</div> : null}
      </div>
      {selected ? (
        <svg
          width={13}
          height={13}
          viewBox="0 0 24 24"
          fill="none"
          className="shrink-0 text-text-primary"
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
    </div>
  );
}

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${Math.round(tokens / 100_000) / 10}M context`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1000)}K context`;
  return `${tokens} context`;
}

function shortLabel(name: string): string {
  return name.replace(/^anthropic\//, "").replace(/^openai\//, "").trim();
}
