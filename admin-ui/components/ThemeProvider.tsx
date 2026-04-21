"use client";

import { createContext, useContext, useEffect, useState } from "react";

type Theme = "dark" | "light";

const ThemeCtx = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "dark",
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("dark");

  // Read saved theme on mount. The inline <head> script in layout.tsx has
  // already set data-theme to prevent flash — this effect just syncs React
  // state so useTheme() returns the correct value.
  useEffect(() => {
    try {
      const saved = (localStorage.getItem("sran-theme") as Theme) || "dark";
      setTheme(saved);
      document.documentElement.setAttribute("data-theme", saved);
    } catch {
      /* ignore — localStorage unavailable */
    }
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("sran-theme", next);
    } catch {
      /* ignore */
    }
    document.documentElement.setAttribute("data-theme", next);
  };

  return <ThemeCtx.Provider value={{ theme, toggle }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);
