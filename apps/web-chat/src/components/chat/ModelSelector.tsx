"use client";

// Rich model selector used by ChatInput.
//
// Trigger: a compact pill showing the provider icon + a short model
// name. Click opens an upward-flying dropdown with:
//   - search box (filters by name / provider / description)
//   - "Recent" section (last 3 picked, deduped)
//   - models grouped by category (Flagship / Fast / Reasoning /
//     Specialized / Other) with a section header per bucket
//   - per-row info button → hover/tap popover with description,
//     context window, max output, and pricing
//   - keyboard navigation: ↑/↓ to move the focus, Enter to pick,
//     Escape to dismiss, plain typing to jump into the search box.

import {
  KeyboardEvent,
  MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useModelStore } from "@/stores/modelStore";
import type { ModelCategory, ModelEntry } from "@/lib/types";

import { AssistantAvatar } from "./AssistantAvatar";

interface Props {
  /** Model id displayed in the trigger pill. When omitted, falls
   *  back to modelStore.selectedModelId. Conversation pages pass
   *  the conversation's model_id so the pill reflects the conv's
   *  binding rather than the global default. */
  activeModelId?: string | null;
  disabled?: boolean;
  /** Compact pill mode used inside ChatInput. */
  compact?: boolean;
  /** Called when the user picks a model. Optional — defaults to
   *  ``modelStore.selectModel`` (which updates the global default
   *  + recents). Conversation pages override to additionally PATCH
   *  the conversation's model_id and show a toast. */
  onPick?: (model: ModelEntry) => void;
}

const CATEGORY_ORDER: ModelCategory[] = [
  "flagship",
  "fast",
  "reasoning",
  "specialized",
  "other",
];

const CATEGORY_LABEL: Record<ModelCategory, string> = {
  flagship: "Flagship",
  fast: "Fast",
  reasoning: "Reasoning",
  specialized: "Specialized",
  other: "Other",
};

const CATEGORY_BADGE: Record<ModelCategory, string> = {
  flagship: "bg-blue-500/15 text-blue-300 ring-1 ring-blue-500/30",
  fast: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30",
  reasoning: "bg-purple-500/15 text-purple-300 ring-1 ring-purple-500/30",
  specialized: "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30",
  other: "bg-slate-500/15 text-slate-300 ring-1 ring-slate-500/30",
};

export function ModelSelector({ activeModelId, disabled, compact, onPick }: Props) {
  const models = useModelStore((s) => s.models);
  const selectedId = useModelStore((s) => s.selectedModelId);
  const recentIds = useModelStore((s) => s.recentModelIds);
  const selectModel = useModelStore((s) => s.selectModel);

  const activeId = activeModelId ?? selectedId;
  const activeModel = models.find((m) => m.id === activeId);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const [infoFor, setInfoFor] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Filter pipeline. Search is a simple substring match on name +
  // provider + description; we lowercase once for the query and use
  // the same comparator each frame.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => {
      return (
        m.name.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q)
      );
    });
  }, [models, query]);

  // Recent section: drop the currently-active model so it doesn't
  // appear twice (Recent vs its category). Hidden entirely when
  // there's nothing useful to show.
  const recents = useMemo(() => {
    return recentIds
      .map((id) => models.find((m) => m.id === id))
      .filter((m): m is ModelEntry => Boolean(m) && m!.id !== activeId);
  }, [recentIds, models, activeId]);

  // Grouped: only show buckets that have items AFTER filtering.
  const grouped = useMemo(() => {
    const map = new Map<ModelCategory, ModelEntry[]>();
    for (const m of filtered) {
      const cat = (m.category || "other") as ModelCategory;
      const bucket = map.get(cat) ?? [];
      bucket.push(m);
      map.set(cat, bucket);
    }
    return CATEGORY_ORDER.map((cat) => ({
      cat,
      items: map.get(cat) ?? [],
    })).filter((g) => g.items.length > 0);
  }, [filtered]);

  // Flat list used for keyboard navigation. Order MUST match the
  // visible order (Recent → groups).
  const flat = useMemo(() => {
    const showRecents = !query.trim() && recents.length > 0;
    const list: ModelEntry[] = [];
    if (showRecents) list.push(...recents);
    for (const g of grouped) list.push(...g.items);
    return list;
  }, [grouped, recents, query]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const off = (e: globalThis.MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  // Reset highlight whenever the filter shrinks (so we don't keep
  // pointing at a removed row).
  useEffect(() => {
    setHighlight((prev) =>
      flat.length === 0 ? 0 : Math.min(prev, flat.length - 1),
    );
  }, [flat.length]);

  // Focus the search box on open so the keyboard-flow user can just
  // start typing.
  useEffect(() => {
    if (open) {
      setQuery("");
      setHighlight(0);
      setInfoFor(null);
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open]);

  const pick = useCallback(
    (m: ModelEntry) => {
      if (onPick) {
        onPick(m);
      } else {
        selectModel(m.id);
      }
      setOpen(false);
    },
    [selectModel, onPick],
  );

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((p) => (flat.length ? (p + 1) % flat.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((p) =>
        flat.length ? (p - 1 + flat.length) % flat.length : 0,
      );
    } else if (e.key === "Enter") {
      e.preventDefault();
      const m = flat[highlight];
      if (m) pick(m);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  }

  // Pick a sensible label for the trigger pill.
  const shortName = shortLabel(activeModel?.name ?? activeModel?.id ?? "");

  // Render the trigger button and (conditionally) the dropdown.
  return (
    <div
      ref={ref}
      className="relative"
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`focus-ring flex items-center gap-1.5 rounded-full border border-border text-xs text-text-secondary transition-all hover:bg-bg-elevated hover:text-text-primary disabled:cursor-default disabled:opacity-80 ${
          compact ? "bg-bg-base/40 px-2 py-1" : "bg-bg-secondary px-2.5 py-1.5"
        }`}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Switch model"
      >
        {compact ? <AssistantAvatar modelId={activeId ?? null} size={16} /> : null}
        <span className="font-medium text-text-primary">
          {activeModel ? shortName : "Select model"}
        </span>
        <svg width={10} height={10} viewBox="0 0 24 24" fill="none" aria-hidden className="text-text-muted">
          <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open ? (
        <>
          {/* Mobile-only backdrop — the dropdown reflows into a
              bottom sheet at <640px, so we want a dim layer +
              outside-tap dismissal in that mode. */}
          <div
            aria-hidden
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 bg-black/55 backdrop-blur-sm sm:hidden"
          />
          <div
            role="dialog"
            className="
              popover-appear sheet-up z-40 flex flex-col overflow-hidden
              border border-border bg-bg-surface text-xs shadow-2xl
              fixed inset-x-0 bottom-0 max-h-[80vh] rounded-t-2xl safe-bottom
              sm:absolute sm:bottom-full sm:left-0 sm:inset-x-auto sm:mb-2
              sm:w-[min(320px,90vw)] sm:max-h-[400px] sm:rounded-xl
              sm:sheet-up:animate-none
            "
          >
            <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>
          {/* Search */}
          <div className="border-b border-border/60 px-2.5 py-2">
            <div className="relative">
              <svg
                width={14}
                height={14}
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted"
              >
                <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
                <path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setHighlight(0);
                }}
                placeholder="Search models…"
                className="w-full rounded-md border border-border bg-bg-base/50 py-1.5 pl-8 pr-2 text-xs text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              />
            </div>
          </div>

          {/* List */}
          <div
            ref={listRef}
            className="scrollbar-thin flex-1 overflow-y-auto"
            role="listbox"
            aria-label="Models"
          >
            {flat.length === 0 ? (
              <div className="px-3 py-6 text-center text-text-muted">
                No models match “{query}”.
              </div>
            ) : null}

            {!query.trim() && recents.length > 0 ? (
              <SectionHeader>Recent</SectionHeader>
            ) : null}
            {!query.trim() &&
              recents.map((m, i) => {
                const flatIdx = i;
                return (
                  <ModelRow
                    key={`recent-${m.id}`}
                    model={m}
                    selected={m.id === activeId}
                    highlighted={highlight === flatIdx}
                    infoOpen={infoFor === m.id}
                    onPick={() => pick(m)}
                    onHover={() => setHighlight(flatIdx)}
                    onToggleInfo={(e) => {
                      e.stopPropagation();
                      setInfoFor((p) => (p === m.id ? null : m.id));
                    }}
                  />
                );
              })}

            {grouped.map((group) => {
              // Index offset so highlight indices line up with `flat`.
              const offset =
                (!query.trim() ? recents.length : 0) +
                grouped
                  .slice(0, grouped.indexOf(group))
                  .reduce((acc, g) => acc + g.items.length, 0);
              return (
                <div key={group.cat}>
                  <SectionHeader>{CATEGORY_LABEL[group.cat]}</SectionHeader>
                  {group.items.map((m, i) => {
                    const flatIdx = offset + i;
                    return (
                      <ModelRow
                        key={m.id}
                        model={m}
                        selected={m.id === activeId}
                        highlighted={highlight === flatIdx}
                        infoOpen={infoFor === m.id}
                        onPick={() => pick(m)}
                        onHover={() => setHighlight(flatIdx)}
                        onToggleInfo={(e) => {
                          e.stopPropagation();
                          setInfoFor((p) => (p === m.id ? null : m.id));
                        }}
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>

          {/* Keyboard hint */}
          <div className="border-t border-border/60 px-3 py-1.5 text-[10px] text-text-muted">
            <kbd className="rounded bg-bg-elevated px-1">↑</kbd>
            <kbd className="ml-0.5 rounded bg-bg-elevated px-1">↓</kbd> navigate
            <span className="mx-1.5">·</span>
            <kbd className="rounded bg-bg-elevated px-1">Enter</kbd> select
            <span className="mx-1.5">·</span>
            <kbd className="rounded bg-bg-elevated px-1">Esc</kbd> close
          </div>
        </div>
        </>
      ) : null}
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
      {children}
    </div>
  );
}

interface RowProps {
  model: ModelEntry;
  selected: boolean;
  highlighted: boolean;
  infoOpen: boolean;
  onPick: () => void;
  onHover: () => void;
  onToggleInfo: (e: MouseEvent) => void;
}

function ModelRow({
  model,
  selected,
  highlighted,
  infoOpen,
  onPick,
  onHover,
  onToggleInfo,
}: RowProps) {
  const caps = model.capabilities ?? [];
  return (
    <div
      role="option"
      aria-selected={selected}
      onClick={onPick}
      onMouseEnter={onHover}
      className={`relative flex cursor-pointer items-start gap-2 px-3 py-2 transition-colors ${
        highlighted
          ? "bg-bg-elevated"
          : selected
            ? "bg-bg-elevated/60"
            : "hover:bg-bg-elevated/60"
      }`}
    >
      <AssistantAvatar modelId={model.id} size={22} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium text-text-primary">
            {model.name}
          </span>
          <CategoryBadge category={(model.category || "other") as ModelCategory} />
        </div>
        {model.description ? (
          <div className="mt-0.5 truncate text-[11px] text-text-muted">
            {model.description}
          </div>
        ) : null}
        <div className="mt-1 flex items-center gap-2 text-[10px] text-text-muted">
          {model.context_window > 0 ? (
            <span>Ctx {formatContext(model.context_window)}</span>
          ) : null}
          <CapabilityIcons capabilities={caps} />
        </div>
      </div>
      <div className="flex items-start gap-1">
        {selected ? (
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill="none"
            className="mt-1 shrink-0 text-accent"
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
        <button
          type="button"
          aria-label="Model info"
          onClick={onToggleInfo}
          className="focus-ring rounded p-1 text-text-muted transition-colors hover:bg-bg-secondary hover:text-text-primary"
        >
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
            <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
            <path d="M12 8v.1M12 11v6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      {infoOpen ? <InfoPopover model={model} /> : null}
    </div>
  );
}

function InfoPopover({ model }: { model: ModelEntry }) {
  return (
    <div
      onClick={(e) => e.stopPropagation()}
      className="popover-appear absolute left-2 right-2 top-full z-10 mt-1 rounded-lg border border-border bg-bg-base p-3 text-[11px] text-text-secondary shadow-2xl"
    >
      <div className="font-medium text-text-primary">{model.name}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide text-text-muted">
        {model.provider}
      </div>
      {model.description ? (
        <p className="mt-1.5 leading-relaxed text-text-secondary">
          {model.description}
        </p>
      ) : null}
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <dt className="text-text-muted">Context</dt>
        <dd className="text-text-primary">
          {model.context_window > 0 ? formatContext(model.context_window) : "—"}
        </dd>
        {model.max_output_tokens ? (
          <>
            <dt className="text-text-muted">Max output</dt>
            <dd className="text-text-primary">
              {formatContext(model.max_output_tokens)}
            </dd>
          </>
        ) : null}
        {typeof model.input_price_per_1m === "number" &&
        model.input_price_per_1m > 0 ? (
          <>
            <dt className="text-text-muted">Input / 1M</dt>
            <dd className="text-text-primary">
              ${model.input_price_per_1m.toFixed(2)}
            </dd>
          </>
        ) : null}
        {typeof model.output_price_per_1m === "number" &&
        model.output_price_per_1m > 0 ? (
          <>
            <dt className="text-text-muted">Output / 1M</dt>
            <dd className="text-text-primary">
              ${model.output_price_per_1m.toFixed(2)}
            </dd>
          </>
        ) : null}
      </dl>
      {model.capabilities && model.capabilities.length > 0 ? (
        <div className="mt-2">
          <div className="text-[10px] uppercase tracking-wide text-text-muted">
            Capabilities
          </div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {model.capabilities.map((c) => (
              <span
                key={c}
                className="rounded-full bg-bg-elevated px-1.5 py-0.5 text-[10px] text-text-secondary"
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CategoryBadge({ category }: { category: ModelCategory }) {
  return (
    <span
      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide ${CATEGORY_BADGE[category]}`}
    >
      {CATEGORY_LABEL[category]}
    </span>
  );
}

function CapabilityIcons({ capabilities }: { capabilities: string[] }) {
  const has = (k: string) => capabilities.includes(k);
  return (
    <span className="flex items-center gap-1.5">
      {has("vision") ? (
        <span title="Vision" aria-label="Vision">
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"
              stroke="currentColor"
              strokeWidth="1.7"
            />
            <circle cx="12" cy="12" r="2.5" stroke="currentColor" strokeWidth="1.7" />
          </svg>
        </span>
      ) : null}
      {has("function_calling") ? (
        <span title="Function calling" aria-label="Function calling">
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M14 7h4v4M10 17H6v-4M21 3l-9 9M3 21l9-9"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      ) : null}
      {has("reasoning") ? (
        <span title="Reasoning" aria-label="Reasoning">
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M9 21h6M12 17v4M12 3a6 6 0 0 1 4 10.5V16H8v-2.5A6 6 0 0 1 12 3Z"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      ) : null}
    </span>
  );
}

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${Math.round(tokens / 100_000) / 10}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

function shortLabel(name: string): string {
  return name.replace(/^anthropic\//, "").replace(/^openai\//, "").trim();
}
