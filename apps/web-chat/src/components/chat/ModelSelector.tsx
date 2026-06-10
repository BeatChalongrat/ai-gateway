"use client";

// Model selector — Claude-style trigger pill + sectioned picker.
//
// Trigger pill: model name + chevron (Sparkles + "Auto" when the Auto
// sentinel is active). The backend receives the literal "auto" string
// and runs its own per-request resolution.
//
// Picker content (the catalog now runs to 100+ models, dynamic from the
// Gateway): a search box plus sections — Auto, Popular (most-selected),
// Recently used, then per-provider groups (3 preview rows + an expand
// affordance). Popular/Recent are tracked locally in localStorage. The
// outer shell (mobile bottom sheet / desktop popover, backdrop, theme
// tokens) is unchanged from the previous flat-list version.

import { useEffect, useMemo, useRef, useState } from "react";
import { BrainCircuit, ImagePlus, Search, Sparkles, X } from "lucide-react";

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

// Synthetic ModelEntry used to fire onPick when the user clicks Auto.
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

// Preview rows shown per provider group before the "…N more" expander.
const PROVIDER_PREVIEW_COUNT = 3;
const POPULAR_COUNT = 4;
const RECENT_COUNT = 3;

// ─── Local usage/recency tracking (localStorage) ────────────────
// Powers the Popular + Recently-used sections without a backend call.
// All access is guarded so SSR (window undefined) and disabled storage
// degrade to "no history" rather than throwing.

const USAGE_KEY = "sran-model-usage";
const RECENT_KEY = "sran-recent-models";

function readJSON<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJSON(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota exceeded / storage disabled — non-fatal */
  }
}

function trackModelUsage(id: string): void {
  if (id === AUTO_MODEL_ID) return;
  const usage = readJSON<Record<string, number>>(USAGE_KEY, {});
  usage[id] = (usage[id] || 0) + 1;
  writeJSON(USAGE_KEY, usage);
}

function trackRecentModel(id: string): void {
  if (id === AUTO_MODEL_ID) return;
  const recent = readJSON<string[]>(RECENT_KEY, []);
  writeJSON(RECENT_KEY, [id, ...recent.filter((x) => x !== id)].slice(0, RECENT_COUNT + 2));
}

function filterModels(models: ModelEntry[], query: string): ModelEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return models;
  return models.filter(
    (m) =>
      m.name.toLowerCase().includes(q) ||
      m.id.toLowerCase().includes(q) ||
      (m.provider || "").toLowerCase().includes(q),
  );
}

export function ModelSelector({ activeModelId, disabled, compact, onPick }: Props) {
  const t = useT();
  const models = useModelStore((s) => s.models);
  const selectedId = useModelStore((s) => s.selectedModelId);
  const selectModel = useModelStore((s) => s.selectModel);
  const selectAuto = useModelStore((s) => s.selectAuto);

  const activeId = activeModelId ?? selectedId;
  const isAuto = activeId === AUTO_MODEL_ID;
  const activeModel = isAuto ? null : models.find((m) => m.id === activeId);

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click (mousedown so it fires before the trigger's
  // own click toggles it back open).
  useEffect(() => {
    if (!open) return;
    const off = (e: globalThis.MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  // Lazy-load the catalog the first time the picker opens.
  useEffect(() => {
    if (!open) return;
    const s = useModelStore.getState();
    if (!s.loaded && !s.loading) void s.loadModels();
  }, [open]);

  const pickAuto = () => {
    if (onPick) onPick(AUTO_PICK_ENTRY);
    else selectAuto();
    setOpen(false);
  };

  const pickModel = (m: ModelEntry) => {
    trackModelUsage(m.id);
    trackRecentModel(m.id);
    if (onPick) onPick(m);
    else selectModel(m.id);
    setOpen(false);
  };

  const triggerLabel = isAuto
    ? t("model.auto.name")
    : activeModel
      ? shortLabel(activeModel.name ?? activeModel.id)
      : t("model.selectModel");

  return (
    <div ref={ref} className="relative">
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
              sm:w-[min(320px,92vw)] sm:max-h-[420px] sm:rounded-xl
              sm:sheet-up:animate-none
            "
          >
            <div className="flex justify-center pt-2 sm:hidden" aria-hidden>
              <span className="h-1 w-10 rounded-full bg-border" />
            </div>

            <ModelPicker
              models={models}
              activeId={activeId}
              isAuto={isAuto}
              onPickAuto={pickAuto}
              onPickModel={pickModel}
              t={t}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

// ─── Picker content ─────────────────────────────────────────────
// Mounted only while the popover is open, so search + expanded-group
// state resets cleanly on every open.

function ModelPicker({
  models,
  activeId,
  isAuto,
  onPickAuto,
  onPickModel,
  t,
}: {
  models: ModelEntry[];
  activeId: string | null;
  isAuto: boolean;
  onPickAuto: () => void;
  onPickModel: (m: ModelEntry) => void;
  t: ReturnType<typeof useT>;
}) {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  const loaded = useModelStore((s) => s.loaded);
  const loading = useModelStore((s) => s.loading);

  // Autofocus the search on desktop only — on touch this would pop the
  // on-screen keyboard over the bottom sheet, which is jarring.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.matchMedia("(pointer: fine)").matches) {
      inputRef.current?.focus();
    }
  }, []);

  const filtered = useMemo(() => filterModels(models, search), [models, search]);
  const searching = search.trim().length > 0;

  // Popular: most-selected (usage>0), desc, top N. Recent: last-selected
  // order. Both read once per render from localStorage; only shown when
  // not actively searching.
  const popular = useMemo<ModelEntry[]>(() => {
    if (searching) return [];
    const usage = readJSON<Record<string, number>>(USAGE_KEY, {});
    return models
      .filter((m) => (usage[m.id] || 0) > 0)
      .sort((a, b) => (usage[b.id] || 0) - (usage[a.id] || 0))
      .slice(0, POPULAR_COUNT);
  }, [models, searching]);

  const recent = useMemo<ModelEntry[]>(() => {
    if (searching) return [];
    const ids = readJSON<string[]>(RECENT_KEY, []);
    return ids
      .map((id) => models.find((m) => m.id === id))
      .filter((m): m is ModelEntry => Boolean(m))
      .slice(0, RECENT_COUNT);
  }, [models, searching]);

  const providerGroups = useMemo(() => {
    const groups = new Map<string, ModelEntry[]>();
    for (const m of filtered) {
      const p = m.provider || "other";
      if (!groups.has(p)) groups.set(p, []);
      groups.get(p)!.push(m);
    }
    // Most-populated providers first.
    return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [filtered]);

  const toggle = (p: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  const showAuto = !searching || "auto".includes(search.trim().toLowerCase());
  const noModels = models.length === 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Search — always visible, pinned above the scroll area. */}
      <div className="shrink-0 border-b border-border p-2">
        <div className="flex items-center gap-2 rounded-lg bg-bg-base px-2.5 py-1.5">
          <Search width={14} height={14} className="shrink-0 text-text-tertiary" aria-hidden />
          <input
            ref={inputRef}
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && search) {
                e.stopPropagation();
                setSearch("");
              }
            }}
            placeholder={t("model.searchPlaceholder")}
            className="min-w-0 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
          />
          {search ? (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearch("")}
              className="focus-ring shrink-0 rounded text-text-tertiary hover:text-text-primary"
            >
              <X width={14} height={14} aria-hidden />
            </button>
          ) : null}
        </div>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto py-1">
        {/* Auto — always first (unless filtered out by search). */}
        {showAuto ? (
          <ModelRow
            label={t("model.auto.name")}
            sub={t("model.auto.description")}
            selected={isAuto}
            onClick={onPickAuto}
            leading={<Sparkles width={13} height={13} className="text-text-tertiary" aria-hidden />}
          />
        ) : null}

        {/* Loading / empty states. */}
        {noModels ? (
          <div className="px-3 py-6 text-center text-text-muted">
            {loading || !loaded ? (
              <span className="inline-flex items-center gap-2">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
                Loading…
              </span>
            ) : (
              <>No models available.</>
            )}
          </div>
        ) : null}

        {popular.length > 0 ? (
          <Section title={t("model.popular")}>
            {popular.map((m) => (
              <ModelRow
                key={`pop-${m.id}`}
                label={m.name}
                sub={m.provider}
                selected={!isAuto && m.id === activeId}
                onClick={() => onPickModel(m)}
                reasoning={Boolean(m.reasoning?.supported)}
                imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
              />
            ))}
          </Section>
        ) : null}

        {recent.length > 0 ? (
          <Section title={t("model.recent")}>
            {recent.map((m) => (
              <ModelRow
                key={`rec-${m.id}`}
                label={m.name}
                sub={m.provider}
                selected={!isAuto && m.id === activeId}
                onClick={() => onPickModel(m)}
                reasoning={Boolean(m.reasoning?.supported)}
                imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
              />
            ))}
          </Section>
        ) : null}

        {providerGroups.map(([provider, group]) => {
          const isExpanded = searching || expanded.has(provider);
          const preview = isExpanded ? group : group.slice(0, PROVIDER_PREVIEW_COUNT);
          const hiddenCount = group.length - preview.length;
          return (
            <Section
              key={provider}
              title={provider}
              count={group.length}
            >
              {preview.map((m) => (
                <ModelRow
                  key={`${provider}-${m.id}`}
                  label={m.name}
                  selected={!isAuto && m.id === activeId}
                  onClick={() => onPickModel(m)}
                  reasoning={Boolean(m.reasoning?.supported)}
                  imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
                />
              ))}
              {hiddenCount > 0 ? (
                <button
                  type="button"
                  onClick={() => toggle(provider)}
                  className="focus-ring w-full rounded-md px-3 py-2 text-left text-[12px] text-text-tertiary transition-colors hover:bg-bg-elevated/60 hover:text-text-secondary"
                >
                  …{hiddenCount} more
                </button>
              ) : null}
            </Section>
          );
        })}

        {/* Search yielded nothing (Auto already hidden in this branch). */}
        {searching && filtered.length === 0 && !showAuto ? (
          <div className="px-3 py-8 text-center text-text-muted">No models found</div>
        ) : null}
      </div>
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-border first:border-t-0">
      <div className="flex items-center justify-between px-3 pb-1 pt-2">
        <div className="flex min-w-0 items-center">
          <span className="truncate text-[10px] font-medium uppercase tracking-wider text-text-tertiary">
            {title}
          </span>
        </div>
        {count ? <span className="shrink-0 text-[10px] text-text-tertiary">{count}</span> : null}
      </div>
      <div>{children}</div>
    </div>
  );
}

interface RowProps {
  label: string;
  sub?: string;
  selected: boolean;
  onClick: () => void;
  leading?: React.ReactNode;
  /** T4 — show a "thinks" badge when the model advertises reasoning. */
  reasoning?: boolean;
  /** Phase C — show an image badge when the model can generate images. */
  imageGen?: boolean;
}

function ModelRow({ label, sub, selected, onClick, leading, reasoning, imageGen }: RowProps) {
  const t = useT();
  return (
    <div
      role="option"
      aria-selected={selected}
      onClick={onClick}
      className={`flex cursor-pointer items-center gap-2 px-3 py-2 transition-colors ${
        selected ? "bg-bg-elevated/60" : "hover:bg-bg-elevated/60"
      }`}
    >
      {leading ? (
        <span className="inline-flex shrink-0 items-center">{leading}</span>
      ) : null}
      <div className="min-w-0 flex-1">
        <span className="text-sm font-medium text-text-primary">{label}</span>
        {sub ? <span className="ml-1.5 text-[11px] text-text-muted">{sub}</span> : null}
      </div>
      {reasoning ? (
        <span
          title={t("model.reasoningBadge")}
          aria-label={t("model.reasoningBadge")}
          className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-blue-500/15 px-1.5 py-0.5 text-[10px] font-medium text-blue-400"
        >
          <BrainCircuit className="h-3 w-3" aria-hidden />
          {t("model.reasoningBadge")}
        </span>
      ) : null}
      {imageGen ? (
        <span
          title={t("model.imageBadge")}
          aria-label={t("model.imageBadge")}
          className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-400"
        >
          <ImagePlus className="h-3 w-3" aria-hidden />
          {t("model.imageBadge")}
        </span>
      ) : null}
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

function shortLabel(name: string): string {
  return name.replace(/^anthropic\//, "").replace(/^openai\//, "").trim();
}
