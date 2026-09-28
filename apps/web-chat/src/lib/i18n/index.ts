"use client";

// Tiny i18n primitive — Zustand store + ``useT()`` hook.
//
// Why a hand-rolled implementation and not next-intl / react-i18next?
//   * No router segments, no server-rendered locale routing — the
//     web chat is a single SPA shell after the initial load.
//   * Zero new npm deps; the dictionary is a typed object.
//   * The translation surface is small (~80 keys) so the cost of
//     a library doesn't earn back its bundle weight.
//
// Hydration story — same shape as themeStore: the SSR pass + first
// client paint both render the default locale (``en``). After mount,
// ``hydrate()`` reads localStorage and swaps in the user's choice.
// React doesn't complain because the initial HTML matches; only
// post-hydration re-renders see Thai. The locale change also writes
// ``lang`` on <html> so screen readers + Google Translate detect it.

import { createContext, createElement, useContext, useEffect, type ReactNode } from "react";
import { create } from "zustand";

import { dictionaries, type DictKey, type Locale } from "./dict";

interface I18nState {
  locale: Locale;
  hydrated: boolean;
  setLocale: (l: Locale) => void;
  toggle: () => void;
  hydrate: () => void;
}

const STORAGE_KEY = "sran-chat-locale";

function readInitial(): Locale {
  if (typeof window === "undefined") return "en";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "en" || raw === "th") return raw;
  } catch {
    /* localStorage disabled */
  }
  // Fall back to navigator.language so a Thai-OS user gets Thai on
  // first run without having to tap the toggle.
  if (
    typeof navigator !== "undefined" &&
    typeof navigator.language === "string" &&
    navigator.language.toLowerCase().startsWith("th")
  ) {
    return "th";
  }
  return "en";
}

function apply(locale: Locale) {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
}

export const useI18nStore = create<I18nState>((set, get) => ({
  locale: "en",
  hydrated: false,
  setLocale(l) {
    apply(l);
    try {
      window.localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
    set({ locale: l });
  },
  toggle() {
    const next: Locale = get().locale === "en" ? "th" : "en";
    get().setLocale(next);
  },
  hydrate() {
    if (get().hydrated) return;
    const initial = readInitial();
    apply(initial);
    set({ locale: initial, hydrated: true });
  },
}));

// LocaleScope — a part of the screen that reads in one language whatever
// the app's language is. A surface written for one audience (a Thai
// accountant's workspace) renders the shared chat components inside it in
// its own language instead of keeping translated copies of them. Outside a
// scope nothing changes: useT follows the store.
const LocaleScopeContext = createContext<Locale | null>(null);

export function LocaleScope({ locale, children }: { locale: Locale; children: ReactNode }) {
  return createElement(LocaleScopeContext.Provider, { value: locale }, children);
}

/** The language this part of the screen reads in: its scope's, else the app's. */
export function useLocale(): Locale {
  const scoped = useContext(LocaleScopeContext);
  const stored = useI18nStore((s) => s.locale);
  return scoped ?? stored;
}

// useT — returns the translation function for the active locale.
// The function falls back to ``en`` for any missing key so a
// partial Thai dictionary degrades gracefully to English instead
// of rendering the raw key.
//
// Optional ``vars`` object substitutes ``{name}``-style placeholders
// — used by the empty-state greeting which needs the user's name.
export function useT(): (key: DictKey, vars?: Record<string, string>) => string {
  const scoped = useContext(LocaleScopeContext);
  const stored = useI18nStore((s) => s.locale);
  const dict = dictionaries[scoped ?? stored];
  return (key, vars) => {
    const raw = dict[key] ?? dictionaries.en[key] ?? key;
    if (!vars) return raw;
    return raw.replace(/\{(\w+)\}/g, (_, name: string) =>
      vars[name] !== undefined ? vars[name] : `{${name}}`,
    );
  };
}

// tOutside — non-hook translation for stores / plain modules (toasts
// fired from zustand actions, error-code mapping). Reads the CURRENT
// locale at call time; unlike useT it does not subscribe, so use it
// only for fire-and-forget strings (toasts, one-shot error text that
// is captured onto a message), never for rendered UI that must
// re-label live on locale switch.
export function tOutside(
  key: DictKey,
  vars?: Record<string, string>,
): string {
  const dict = dictionaries[useI18nStore.getState().locale];
  const raw = dict[key] ?? dictionaries.en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (_, name: string) =>
    vars[name] !== undefined ? vars[name] : `{${name}}`,
  );
}

// Convenience hook used by AppProviders. Runs once on mount so the
// stored locale takes effect before the user interacts.
export function useHydrateI18n() {
  const hydrate = useI18nStore((s) => s.hydrate);
  useEffect(() => {
    hydrate();
  }, [hydrate]);
}

export type { Locale } from "./dict";
