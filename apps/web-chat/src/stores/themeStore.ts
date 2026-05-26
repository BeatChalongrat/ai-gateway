"use client";

// Theme store. Drives the `data-theme` attribute on <html> which
// the CSS-variable palette in globals.css keys off. Persisted to
// localStorage; first paint follows the user's OS preference so
// the SSR markup never flashes the wrong colours.

import { useEffect } from "react";

import { create } from "zustand";

export type Theme = "dark" | "light";

interface ThemeState {
  theme: Theme;
  // True once we've read localStorage / matchMedia on the client.
  // Until then the component tree should render the default dark
  // theme to match the SSR pass.
  hydrated: boolean;
  setTheme: (t: Theme) => void;
  toggle: () => void;
  hydrate: () => void;
}

const STORAGE_KEY = "sran-chat-theme";

function readInitial(): Theme {
  if (typeof window === "undefined") return "dark";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
  } catch {
    /* localStorage disabled */
  }
  if (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: light)").matches
  ) {
    return "light";
  }
  return "dark";
}

function apply(theme: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: "dark",
  hydrated: false,
  setTheme(t) {
    apply(t);
    try {
      window.localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* ignore */
    }
    set({ theme: t });
  },
  toggle() {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    get().setTheme(next);
  },
  hydrate() {
    if (get().hydrated) return;
    const initial = readInitial();
    apply(initial);
    set({ theme: initial, hydrated: true });
  },
}));

// Convenience hook used by AppProviders. Runs once on mount so the
// stored preference takes effect before the user sees anything.
export function useHydrateTheme() {
  const hydrate = useThemeStore((s) => s.hydrate);
  useEffect(() => {
    hydrate();
  }, [hydrate]);
}
