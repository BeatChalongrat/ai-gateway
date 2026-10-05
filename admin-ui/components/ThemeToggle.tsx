"use client";

import { useEffect, useState } from "react";
import { useTheme } from "./ThemeProvider";
import { useT } from "../lib/i18n";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const t = useT();
  const label = t(theme === "dark" ? "app.theme.toLight" : "app.theme.toDark");
  // Don't render SVG until after hydration — the sun/moon depends on the
  // resolved theme, which only becomes accurate once the client effect in
  // ThemeProvider reads localStorage. Rendering a neutral placeholder
  // prevents a hydration mismatch flicker.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <button
      onClick={toggle}
      aria-label={label}
      title={label}
      type="button"
      style={{
        width: 30,
        height: 30,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 8,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-default)",
        color: "var(--text-secondary)",
        cursor: "pointer",
        transition: "background-color 120ms ease, color 120ms ease, border-color 120ms ease",
        flexShrink: 0,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--bg-elevated)";
        e.currentTarget.style.color = "var(--text-primary)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "var(--bg-surface)";
        e.currentTarget.style.color = "var(--text-secondary)";
      }}
    >
      {mounted && theme === "dark" ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
        </svg>
      ) : mounted ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ) : (
        <span style={{ width: 13, height: 13 }} aria-hidden />
      )}
    </button>
  );
}
