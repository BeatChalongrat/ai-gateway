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

/** Sentinel id sent to the backend when the user has Auto selected.
 *  The Chat Edge resolver picks a concrete model per-request based
 *  on message complexity, so the frontend never has to predict it. */
export const AUTO_MODEL_ID = "auto";

interface ModelState {
  models: ModelEntry[];
  /** Either a real model id from `models` OR the literal "auto"
   *  sentinel. Backend accepts "auto" verbatim. */
  selectedModelId: string | null;
  recentModelIds: string[];
  loaded: boolean;
  /** True while the loadModels promise is in-flight. */
  loading: boolean;
  /** Derived convenience flag — true whenever `selectedModelId`
   *  equals AUTO_MODEL_ID. Kept on state so subscribers can react
   *  without re-comparing strings. */
  autoMode: boolean;

  loadModels: () => Promise<void>;
  selectModel: (id: string) => void;
  selectAuto: () => void;
  /** Returns the model id used to RENDER the Auto row's "Uses X"
   *  subtitle — first fast-tier model, falling back to the first
   *  available. The backend ignores this; it does its own per-
   *  request resolution. */
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

// readInitialSelection — reads the persisted selected id at module
// init time. Used so the store boots with the user's last pick (or
// AUTO_MODEL_ID if they were last in auto mode) before the catalog
// fetch completes. Without this the model pill flashes "Select
// model" on every reload.
function readInitialSelection(): string | null {
  if (readLocal(AUTO_KEY) === "1") return AUTO_MODEL_ID;
  return readLocal(SELECTED_KEY);
}

export const useModelStore = create<ModelState>((set, get) => ({
  models: [],
  selectedModelId: readInitialSelection(),
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
      const auto = readLocal(AUTO_KEY) === "1";
      const recents = readRecents();
      const pick = auto
        ? AUTO_MODEL_ID
        : stored && allowed.some((m) => m.id === stored)
          ? stored
          : allowed[0]?.id ?? null;
      writeLocal(CACHE_KEY, JSON.stringify(allowed));
      set({
        models: allowed,
        selectedModelId: pick,
        autoMode: auto,
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
      const auto = readLocal(AUTO_KEY) === "1";
      const pick = auto
        ? AUTO_MODEL_ID
        : stored && cached.some((m) => m.id === stored)
          ? stored
          : cached[0]?.id ?? null;
      set({
        models: cached,
        selectedModelId: pick,
        autoMode: auto,
        recentModelIds: readRecents().filter((id) =>
          cached.some((m) => m.id === id),
        ),
        loaded: true,
        loading: false,
      });
    }
  },

  selectModel(id) {
    // Callers that forward the user's raw pick (landing page +
    // conversation page) can hand us the "auto" sentinel — route it
    // through selectAuto so the persisted AUTO_KEY + autoMode stay
    // in lockstep.
    if (id === AUTO_MODEL_ID) {
      get().selectAuto();
      return;
    }
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

  // Auto mode persists AUTO_MODEL_ID as the selected id. The
  // backend treats "auto" as a special sentinel and runs its own
  // per-request resolution based on message complexity, so the
  // frontend never has to predict which concrete model will serve
  // the turn. The Auto row's "Uses X" subtitle is purely advisory.
  selectAuto() {
    writeLocal(SELECTED_KEY, AUTO_MODEL_ID);
    writeLocal(AUTO_KEY, "1");
    set({ selectedModelId: AUTO_MODEL_ID, autoMode: true });
  },

  // Returns the model id the dropdown subtitle uses to describe
  // what Auto would currently pick — first fast-tier, falling back
  // to the first available. Pure read; no state mutation.
  resolveAutoModelId() {
    const all = get().models;
    const fast = all.find((m) => m.category === "fast");
    return (fast?.id ?? all[0]?.id) ?? null;
  },

  getModelById(id) {
    return get().models.find((m) => m.id === id);
  },
}));
