// Available models + the user's current selection + recently-used
// history.
//
// Selection + recents persist per browser via localStorage so a new
// conversation opens on the user's preferred model. Per-conversation
// model is recorded on the Conversation row by the server; this
// store only holds the GLOBAL default, not the conversation default.
//
// Recents track the last 3 distinct models the user picked. They
// appear at the top of the redesigned ModelSelector so frequent
// switchers don't have to re-search every time.

"use client";

import { create } from "zustand";

import { api, ApiError } from "@/lib/api";
import type { ModelEntry } from "@/lib/types";

interface ModelState {
  models: ModelEntry[];
  selectedModelId: string | null;
  recentModelIds: string[];
  loaded: boolean;
  /** True while the loadModels promise is in-flight. */
  loading: boolean;
  /** When true, the trigger pill reads "Auto" instead of the model
   *  name and the backend still receives a concrete id (the first
   *  fast-tier model). Cleared the moment the user picks a real
   *  model from the dropdown. */
  autoMode: boolean;

  loadModels: () => Promise<void>;
  selectModel: (id: string) => void;
  selectAuto: () => string | null;
  resolveAutoModelId: () => string | null;
  getModelById: (id: string) => ModelEntry | undefined;
}

const SELECTED_KEY = "sran-webchat-selected-model";
const RECENT_KEY = "sran-webchat-recent-models";
const AUTO_KEY = "sran-webchat-auto-mode";
const CACHE_KEY = "sran-webchat-models-cache";
const MAX_RECENT = 3;

function readLocal(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* quota or disabled storage — ignore */
  }
}

function readRecents(): string[] {
  const raw = readLocal(RECENT_KEY);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return arr.filter((x) => typeof x === "string");
  } catch {
    /* corrupt cache — drop it */
  }
  return [];
}

function readCachedModels(): ModelEntry[] {
  const raw = readLocal(CACHE_KEY);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return arr as ModelEntry[];
  } catch {
    /* corrupt cache — drop it */
  }
  return [];
}

export const useModelStore = create<ModelState>((set, get) => ({
  models: [],
  selectedModelId: null,
  recentModelIds: [],
  loaded: false,
  loading: false,
  autoMode: readLocal(AUTO_KEY) === "1",

  async loadModels() {
    if (get().loading) return;
    set({ loading: true });
    try {
      const res = await api.models();
      const allowed = res.models.filter((m) => m.allowed);
      const stored = readLocal(SELECTED_KEY);
      const recents = readRecents();
      const pick =
        stored && allowed.some((m) => m.id === stored)
          ? stored
          : allowed[0]?.id ?? null;
      writeLocal(CACHE_KEY, JSON.stringify(allowed));
      set({
        models: allowed,
        selectedModelId: pick,
        recentModelIds: recents.filter((id) => allowed.some((m) => m.id === id)),
        loaded: true,
        loading: false,
      });
    } catch (err) {
      // Network / 5xx — fall back to cached models so the UI still
      // renders a picker. 401 path is handled inside ApiClient
      // (redirects to Portal /login), so we just bail.
      if (err instanceof ApiError && err.status === 401) {
        set({ loading: false });
        return;
      }
      const cached = readCachedModels();
      const stored = readLocal(SELECTED_KEY);
      const pick =
        stored && cached.some((m) => m.id === stored)
          ? stored
          : cached[0]?.id ?? null;
      set({
        models: cached,
        selectedModelId: pick,
        recentModelIds: readRecents().filter((id) =>
          cached.some((m) => m.id === id),
        ),
        loaded: true,
        loading: false,
      });
    }
  },

  selectModel(id) {
    writeLocal(SELECTED_KEY, id);
    writeLocal(AUTO_KEY, "0");
    set((s) => {
      // Bump the chosen id to the front of recents, dedup, cap to 3.
      const next = [id, ...s.recentModelIds.filter((x) => x !== id)].slice(
        0,
        MAX_RECENT,
      );
      writeLocal(RECENT_KEY, JSON.stringify(next));
      return { selectedModelId: id, recentModelIds: next, autoMode: false };
    });
  },

  // Picks the first fast-tier model and flags the store as auto. The
  // pill displays "Auto" until the user picks a concrete model from
  // the dropdown. Backend always sees a real model id.
  selectAuto() {
    const resolved = get().resolveAutoModelId();
    if (!resolved) return null;
    writeLocal(SELECTED_KEY, resolved);
    writeLocal(AUTO_KEY, "1");
    set((s) => ({
      selectedModelId: resolved,
      autoMode: true,
      recentModelIds: s.recentModelIds,
    }));
    return resolved;
  },

  // Resolves "auto" to a concrete model id without touching state.
  // Preference order: first fast-tier → first model overall.
  resolveAutoModelId() {
    const all = get().models;
    const fast = all.find((m) => m.category === "fast");
    return (fast?.id ?? all[0]?.id) ?? null;
  },

  getModelById(id) {
    return get().models.find((m) => m.id === id);
  },
}));
