"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type Lang = "th" | "en";

const STORAGE_KEY = "sran-landing-lang";

const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: "th",
  setLang: () => {},
});

// Thai is the SSR default so the no-JS page and the first paint are
// complete in Thai; a saved "en" choice is applied after hydration.
export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("th");

  useEffect(() => {
    try {
      if (localStorage.getItem(STORAGE_KEY) === "en") setLangState("en");
    } catch {
      /* storage unavailable (private mode) — keep default */
    }
  }, []);

  // The root layout stamps lang="en" on <html>; while a marketing page
  // is mounted the attribute must follow the visitor's choice so
  // screen readers and hyphenation pick the right language.
  useEffect(() => {
    const previous = document.documentElement.lang;
    document.documentElement.lang = lang;
    return () => {
      document.documentElement.lang = previous;
    };
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* non-persistent is fine */
    }
  }, []);

  return (
    <LangContext.Provider value={{ lang, setLang }}>
      {children}
    </LangContext.Provider>
  );
}

export function useLang() {
  return useContext(LangContext);
}
