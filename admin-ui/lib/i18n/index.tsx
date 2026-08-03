"use client";

// i18n primitive for the admin console.
//
// Mirrors apps/web-chat/src/lib/i18n (typed dictionary, `t()` with `{var}`
// substitution, localStorage persistence, navigator.language first-run
// default) with one deliberate difference: the store is a React context,
// not a Zustand store, because admin-ui has no Zustand dependency and
// already uses exactly this context shape for ThemeProvider. Matching the
// app we are in beats matching the app we copied from.
//
// Hydration: SSR and the first client paint both render `en`, then the
// mount effect swaps in the stored locale — same trick ThemeProvider uses,
// so React never sees a server/client text mismatch.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { dictionaries, type DictKey, type Locale } from "./dict";

const STORAGE_KEY = "sran-admin-locale";

type Translate = (key: DictKey, vars?: Record<string, string | number>) => string;

interface I18nValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  toggle: () => void;
  t: Translate;
}

function translate(locale: Locale, key: DictKey, vars?: Record<string, string | number>): string {
  // Fall back to English (then to the key itself) so a missing string can
  // never render as blank space in a table cell.
  const raw = dictionaries[locale][key] ?? dictionaries.en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (whole, name: string) =>
    vars[name] !== undefined ? String(vars[name]) : whole,
  );
}

const I18nCtx = createContext<I18nValue>({
  locale: "en",
  setLocale: () => {},
  toggle: () => {},
  t: (key, vars) => translate("en", key, vars),
});

function readInitial(): Locale {
  if (typeof window === "undefined") return "en";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "en" || raw === "th") return raw;
  } catch {
    /* localStorage unavailable */
  }
  if (
    typeof navigator !== "undefined" &&
    typeof navigator.language === "string" &&
    navigator.language.toLowerCase().startsWith("th")
  ) {
    return "th";
  }
  return "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    const initial = readInitial();
    setLocaleState(initial);
    document.documentElement.lang = initial;
  }, []);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      window.localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = l;
  }, []);

  const value = useMemo<I18nValue>(
    () => ({
      locale,
      setLocale,
      toggle: () => setLocale(locale === "en" ? "th" : "en"),
      t: (key, vars) => translate(locale, key, vars),
    }),
    [locale, setLocale],
  );

  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>;
}

/// Full context — for the language switcher.
export const useI18n = () => useContext(I18nCtx);

/// Just the translate function, which is what nearly every call site wants.
export const useT = (): Translate => useContext(I18nCtx).t;

export type { DictKey, Locale };
