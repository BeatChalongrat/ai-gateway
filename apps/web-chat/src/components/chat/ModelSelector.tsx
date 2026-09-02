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
import { hasToolsCap, hasVisionCap } from "@/lib/visionGate";
import { BrainCircuit, ImagePlus, Search, Sparkles, X, Eye } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
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
  /** FIX47 Part C4 — what the message being composed needs. A model that
   *  cannot serve it is shown disabled with the reason, instead of being
   *  pickable and failing after send (the ChatGPT / Claude behaviour). */
  needsVision?: boolean;
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

// Sprint 3 — dedicated media-model kind sections (shown above provider
// groups so video/audio/tts models are discoverable even at 0 count).
// Section labels resolve through the dict at render (T4).
const MEDIA_KIND_SECTIONS: { kind: "video" | "audio" | "tts"; labelKey: DictKey }[] = [
  { kind: "video", labelKey: "model.section.video" },
  { kind: "audio", labelKey: "model.section.audio" },
  { kind: "tts", labelKey: "model.section.tts" },
];

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

export function ModelSelector({ activeModelId, disabled, compact, onPick, needsVision }: Props) {
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
            aria-label={t("a11y.models")}
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
              needsVision={Boolean(needsVision)}
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
  models: allModels,
  activeId,
  isAuto,
  onPickAuto,
  onPickModel,
  needsVision,
  t,
}: {
  models: ModelEntry[];
  activeId: string | null;
  isAuto: boolean;
  onPickAuto: () => void;
  onPickModel: (m: ModelEntry) => void;
  needsVision: boolean;
  t: ReturnType<typeof useT>;
}) {
  const [search, setSearch] = useState("");
  // FIX47 Part C — the picker offers the models an admin marked
  // show_in_chat (plus the one this conversation already uses, so the
  // pill can still name it). The rest stay in the store for lookups.
  const models = useMemo(
    () => allModels.filter((m) => m.show_in_chat !== false || m.id === activeId),
    [allModels, activeId],
  );
  // FIX47 Part C4 — per-row state for the message being composed: a
  // model that cannot read the attached image is disabled with the
  // reason. FIX47.6 Part C — the line under a model without tool
  // support is GUIDANCE (what it's for), never a defect label: the old
  // "no tools" read as broken; "great for general Q&A" reads as
  // specialised.
  const rowState = (m: ModelEntry) => {
    const chat = !m.model_kind || m.model_kind === "chat";
    const blocked = chat && needsVision && !hasVisionCap(m);
    return {
      disabled: blocked,
      disabledReason: blocked ? t("model.cantReadImages") : undefined,
      sub: chat && !hasToolsCap(m) ? `${m.provider} · ${t("model.subGeneralQA")}` : m.provider,
    };
  };
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

  // Sprint 3 — dedicated media models (model_kind video/audio/tts) get their
  // own kind sections; the provider groups below carry chat models only.
  const mediaByKind = useMemo(
    () => ({
      video: filtered.filter((m) => m.model_kind === "video"),
      audio: filtered.filter((m) => m.model_kind === "audio"),
      tts: filtered.filter((m) => m.model_kind === "tts"),
    }),
    [filtered],
  );

  const providerGroups = useMemo(() => {
    const groups = new Map<string, ModelEntry[]>();
    for (const m of filtered) {
      // Media models render in their kind sections, not the provider groups.
      if (m.model_kind && m.model_kind !== "chat") continue;
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
              aria-label={t("a11y.clearSearch")}
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
                {t("common.loading")}
              </span>
            ) : (
              <>{t("model.empty")}</>
            )}
          </div>
        ) : null}

        {popular.length > 0 ? (
          <Section title={t("model.popular")}>
            {popular.map((m) => (
              <ModelRow
                key={`pop-${m.id}`}
                label={m.name}
                {...rowState(m)}
                selected={!isAuto && m.id === activeId}
                onClick={() => onPickModel(m)}
                reasoning={Boolean(m.reasoning?.supported)}
                imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
                vision={hasVisionCap(m)}
                mediaKind={m.model_kind && m.model_kind !== "chat" ? m.model_kind : undefined}
                description={m.description}
                contextWindow={m.context_window}
                inputPrice={m.input_price_per_1m}
                outputPrice={m.output_price_per_1m}
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
                {...rowState(m)}
                selected={!isAuto && m.id === activeId}
                onClick={() => onPickModel(m)}
                reasoning={Boolean(m.reasoning?.supported)}
                imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
                vision={hasVisionCap(m)}
                mediaKind={m.model_kind && m.model_kind !== "chat" ? m.model_kind : undefined}
                description={m.description}
                contextWindow={m.context_window}
                inputPrice={m.input_price_per_1m}
                outputPrice={m.output_price_per_1m}
              />
            ))}
          </Section>
        ) : null}

        {/* Media-model kind sections (Sprint 3). Shown even at 0 count when
            not searching, so users discover the video/audio/tts verticals and
            learn a key is needed. Hidden when searching unless they match. */}
        {MEDIA_KIND_SECTIONS.map(({ kind, labelKey }) => {
          const items = mediaByKind[kind];
          if (searching && items.length === 0) return null;
          return (
            <Section
              key={`kind-${kind}`}
              title={`${MEDIA_BADGE[kind]} ${t(labelKey)}`}
              count={items.length || undefined}
            >
              {items.length > 0 ? (
                items.map((m) => (
                  <ModelRow
                    key={`kind-${kind}-${m.id}`}
                    label={m.name}
                    {...rowState(m)}
                    selected={!isAuto && m.id === activeId}
                    onClick={() => onPickModel(m)}
                    mediaKind={kind}
                    tier={m.cost_tier}
                    description={m.description}
                contextWindow={m.context_window}
                inputPrice={m.input_price_per_1m}
                outputPrice={m.output_price_per_1m}
                  />
                ))
              ) : (
                <p className="px-3 py-2 text-[11px] text-text-muted">
                  {t("model.emptyKind")}
                </p>
              )}
            </Section>
          );
        })}

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
                  {...rowState(m)}
                  selected={!isAuto && m.id === activeId}
                  onClick={() => onPickModel(m)}
                  reasoning={Boolean(m.reasoning?.supported)}
                  imageGen={Boolean(m.capabilities.includes("image_generation_via_tools") || m.capabilities.includes("image_generation"))}
                vision={hasVisionCap(m)}
                  mediaKind={m.model_kind && m.model_kind !== "chat" ? m.model_kind : undefined}
                  description={m.description}
                contextWindow={m.context_window}
                inputPrice={m.input_price_per_1m}
                outputPrice={m.output_price_per_1m}
                />
              ))}
              {hiddenCount > 0 ? (
                <button
                  type="button"
                  onClick={() => toggle(provider)}
                  className="focus-ring w-full rounded-md px-3 py-2 text-left text-[12px] text-text-tertiary transition-colors hover:bg-bg-elevated/60 hover:text-text-secondary"
                >
                  {t("model.showMore", { n: String(hiddenCount) })}
                </button>
              ) : null}
            </Section>
          );
        })}

        {/* Search yielded nothing (Auto already hidden in this branch). */}
        {searching && filtered.length === 0 && !showAuto ? (
          <div className="px-3 py-8 text-center text-text-muted">{t("model.noResults")}</div>
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
  /** FIX47 Part C4 — not pickable for the message being composed. */
  disabled?: boolean;
  disabledReason?: string;
  leading?: React.ReactNode;
  /** T4 — show a "thinks" badge when the model advertises reasoning. */
  reasoning?: boolean;
  /** Phase C — show an image badge when the model can generate images. */
  imageGen?: boolean;
  /** FIX27 — "Reads images": the model accepts image input. Derived from the
   *  same capability string the attach gate reads (visionGate.hasVisionCap),
   *  so the badge and the gate cannot disagree. */
  vision?: boolean;
  /** Sprint 2A.5 — media model kind badge (🎬 video / 🎵 audio / 🔊 tts). */
  mediaKind?: "video" | "audio" | "tts";
  /** Sprint 3 — coarse cost-tier badge. Dormant until FIX35 Part C, when the
   *  gateway started serving ``cost_tier`` (five bands) — now live. */
  tier?: "free" | "low" | "medium" | "high" | "xhigh" | "max";
  /** Phase 11 — the catalog has served this since day one but nothing
   *  rendered it, so users picked models blind. Second line under the name:
   *  what the model is good at.
   *
   *  Phase 13.1 removed the context/pricing line under this one ("internal
   *  tool on a shared account — token economics was noise"). FIX33 B1 puts
   *  it back, quieter: the header already shows credits (CreditBadge) and the
   *  usage panel bills per model, so the price per 1M tokens is the same
   *  economics the user already sees, and the context window is what the
   *  FIX33 context meter budgets against. Rendered only when the catalog
   *  serves the numbers; one muted line, never a badge. */
  description?: string;
  /** FIX33 B1 — tokens of context the model accepts (catalog context_window). */
  contextWindow?: number;
  /** FIX33 B1 — USD per 1M input / output tokens (catalog *_price_per_1m). */
  inputPrice?: number;
  outputPrice?: number;
}

/** "128K" / "1M" — the context window in a glance. */
export function formatContextWindow(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return String(tokens);
}

/** "$2.50 / $10.00" — trims to the precision the number needs. */
export function formatPricePair(input: number, output: number): string {
  const f = (n: number) => {
    const s = n >= 1 ? n.toFixed(2) : n.toFixed(3);
    return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
  };
  return `$${f(input)} / $${f(output)}`;
}

/** FIX35 Part A — is this catalog description worth a line in the picker?
 *
 *  chat-edge fills uncurated rows with a provider placeholder ("OpenAI chat
 *  model.", "Anthropic Claude model.") so the dropdown never renders blank.
 *  That text says nothing the provider line under the name doesn't already
 *  say, so it is noise. The rule is structural, not a per-model list: a
 *  description earns its line when it is a real sentence — at least five
 *  words — and is not the "<Provider> [<family>] model." template. Curated
 *  copy ("OpenAI's most capable multimodal flagship. Great for complex
 *  tasks, vision, and tool use.") passes; every placeholder fails. */
export function isFillerDescription(description: string | undefined | null): boolean {
  const text = (description ?? "").trim();
  if (!text) return true;
  const words = text.split(/\s+/);
  if (words.length < 5) return true;
  // chat-edge's second generated placeholder, for models the gateway
  // discovered at runtime: "<Name> is available in this workspace."
  if (/\bis available in this workspace\.?$/i.test(text)) return true;
  return /^\S+(?:\s+\S+){0,2}\s+(?:chat\s+)?model\.?$/i.test(text);
}

/** FIX35 Part A — the one-line hover tooltip that replaced the permanent
 *  context/price line (FIX33 B1). The numbers are the same economics the
 *  CreditBadge, the usage panel and the FIX33 context meter already show;
 *  repeating them on every row was noise, so they live on hover only —
 *  never as a permanent line. */
export function formatModelTooltip(
  t: (key: DictKey, vars?: Record<string, string>) => string,
  caps: string[],
  contextWindow?: number,
  inputPrice?: number,
  outputPrice?: number,
): string | undefined {
  const parts = [...caps];
  if (contextWindow && contextWindow > 0) parts.push(t("model.contextWindow", { n: formatContextWindow(contextWindow) }));
  if (typeof inputPrice === "number" && typeof outputPrice === "number") {
    parts.push(t("model.price", { pair: formatPricePair(inputPrice, outputPrice) }));
  }
  return parts.length ? parts.join(" · ") : undefined;
}

const MEDIA_BADGE: Record<"video" | "audio" | "tts", string> = {
  video: "🎬",
  audio: "🎵",
  tts: "🔊",
};

// Dict keys, resolved with t() where rendered (T4).
const TIER_LABEL_KEY: Record<"free" | "low" | "medium" | "high" | "xhigh" | "max", DictKey> = {
  free: "model.tier.free",
  low: "model.tier.low",
  medium: "model.tier.medium",
  high: "model.tier.high",
  xhigh: "model.tier.xhigh",
  max: "model.tier.max",
};

function ModelRow({
  label, sub, selected, onClick, leading, reasoning, imageGen, vision, mediaKind, tier,
  description, contextWindow, inputPrice, outputPrice, disabled, disabledReason,
}: RowProps) {
  const t = useT();
  // FIX29 §2.1 — capabilities as a tooltip on the name, not as pills.
  const caps = [
    reasoning ? t("model.reasoningBadge") : null,
    vision ? t("model.visionBadge") : null,
    imageGen ? t("model.imageBadge") : null,
  ].filter((c): c is string => Boolean(c));
  const capsTitle = caps.length ? caps.join(" · ") : undefined;
  // FIX35 Part A — context window + price ride the SAME hover tooltip as
  // the capabilities; nothing about token economics is a permanent line.
  const rowTitle = formatModelTooltip(t, caps, contextWindow, inputPrice, outputPrice);
  return (
    <div
      role="option"
      aria-selected={selected}
      aria-disabled={disabled || undefined}
      aria-label={capsTitle ? `${label} — ${capsTitle}` : label}
      onClick={disabled ? undefined : onClick}
      title={disabled ? disabledReason : undefined}
      data-testid={disabled ? "model-row-disabled" : undefined}
      className={`flex items-start gap-2 px-3 py-2 transition-colors ${
        disabled
          ? "cursor-not-allowed opacity-50"
          : selected
            ? "cursor-pointer bg-bg-elevated/60"
            : "cursor-pointer hover:bg-bg-elevated/60"
      }`}
    >
      {leading ? (
        <span className="inline-flex shrink-0 items-center">{leading}</span>
      ) : null}
      {/* FIX29 §2.1 — the name owns its line (block, wraps by word, never
          overlapped); everything else sits in a fixed-width trailing column.
          The "Thinks / Reads images / Makes images" pills were decoration:
          on the live catalog they sat on 63% / 57% / 85% of chat rows, so
          they distinguished nothing and squeezed the name to one word per
          line. They live on as the row tooltip; vision is told at the moment
          it matters (the attach button + the one-click switch, FIX27). */}
      <div className="min-w-0 flex-1">
        <div className="block break-words text-sm font-medium leading-snug text-text-primary" title={rowTitle} data-testid="model-row-name">
          {label}
        </div>
        {sub ? <div className="text-[11px] text-text-muted">{sub}</div> : null}
        {disabled && disabledReason ? (
          <div className="text-[11px] text-status-warning" data-testid="model-row-reason">{disabledReason}</div>
        ) : null}
        {/* FIX35 Part A — a row is the NAME + the PROVIDER. We are a
            multi-provider gateway, so the provider line stays (Claude and
            ChatGPT can drop it: one vendor). The description survives only
            when it says something (isFillerDescription); the FIX33 B1
            context/price line is gone — Phase 13.1 had removed it as noise
            and this round confirms that call: the same numbers already live
            in the CreditBadge, the usage panel and the context meter, so
            here they are hover-only (rowTitle above). Capability badges
            (reasoning / vision / image / media kind) are not noise — they
            say what the model can do — and stay exactly as they were. */}
        {description && !isFillerDescription(description) ? (
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-text-muted" data-testid="model-row-description">
            {description}
          </p>
        ) : null}
      </div>
      <div className="flex w-12 shrink-0 flex-col items-end gap-1">
      {mediaKind ? (
        <span
          title={mediaKind}
          aria-label={mediaKind}
          className="inline-flex shrink-0 items-center rounded-md bg-rose-500/15 px-1.5 py-0.5 text-[11px] font-medium text-rose-400"
        >
          {MEDIA_BADGE[mediaKind]}
        </span>
      ) : null}
      {tier ? (
        <span
          title={t("model.tier.tooltip", { tier: t(TIER_LABEL_KEY[tier]) })}
          className="inline-flex shrink-0 items-center rounded-md bg-bg-elevated px-1.5 py-0.5 text-[10px] font-medium text-text-tertiary"
        >
          {t(TIER_LABEL_KEY[tier])}
        </span>
      ) : null}
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

function shortLabel(name: string): string {
  return name.replace(/^anthropic\//, "").replace(/^openai\//, "").trim();
}
