// Available models + the user's current selection.
//
// Selection persists per browser via localStorage so a new
// conversation starts on the user's preferred model, not a random
// default. Per-conversation model is recorded on the Conversation
// row by the server.

"use client";

import { create } from "zustand";
import { api } from "@/lib/api";
import type { ModelEntry } from "@/lib/types";

interface ModelState {
  models: ModelEntry[];
  selectedModelId: string | null;
  loaded: boolean;
  loadModels: () => Promise<void>;
  selectModel: (id: string) => void;
}

const SELECTED_KEY = "sran-webchat-selected-model";

export const useModelStore = create<ModelState>((set, get) => ({
  models: [],
  selectedModelId: null,
  loaded: false,

  async loadModels() {
    const res = await api.models();
    const allowed = res.models.filter((m) => m.allowed);
    const stored =
      typeof window !== "undefined"
        ? localStorage.getItem(SELECTED_KEY)
        : null;
    const pick =
      stored && allowed.some((m) => m.id === stored)
        ? stored
        : allowed[0]?.id ?? null;
    set({ models: allowed, selectedModelId: pick, loaded: true });
  },

  selectModel(id) {
    if (typeof window !== "undefined") {
      localStorage.setItem(SELECTED_KEY, id);
    }
    set({ selectedModelId: id });
  },
}));
