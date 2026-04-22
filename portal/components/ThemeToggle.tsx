"use client";

import { Moon, Sun } from "lucide-react";

import { useTheme } from "./ThemeProvider";

// Sidebar-scoped colors. The sidebar is navy in BOTH themes, so this
// button uses translucent-white overlays that work on navy regardless
// of theme. Do NOT reference page tokens (--bg-surface, --text-*) —
// those resolve to white on the light-theme page background and would
// paint a white box on the navy sidebar.
//
// Icons are stacked absolutely via .theme-icon; only the `.is-visible`
// one is painted opaque at full scale. Driven by globals.css
// transitions so the swap crossfades + rotates instead of cutting
// instantly.
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";

  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
      type="button"
      style={{
        position: "relative",
        width: "100%",
        height: 32,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        background: "rgba(255,255,255,0.06)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 8,
        color: "rgba(255,255,255,0.8)",
        cursor: "pointer",
        fontSize: 11,
        fontFamily: "inherit",
        transition: "background 0.15s, color 0.15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.1)";
        e.currentTarget.style.color = "#FFFFFF";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.06)";
        e.currentTarget.style.color = "rgba(255,255,255,0.8)";
      }}
    >
      {/* Icon slot — both icons share the same absolute anchor so the
          rotate+scale crossfade lands on the same pixel. The visible
          icon is flipped via .is-visible in globals.css. */}
      <span
        aria-hidden
        style={{
          position: "relative",
          width: 13,
          height: 13,
          display: "inline-block",
        }}
      >
        <span className={`theme-icon ${theme === "dark" ? "is-visible" : ""}`}>
          <Sun size={13} strokeWidth={1.75} />
        </span>
        <span className={`theme-icon ${theme === "light" ? "is-visible" : ""}`}>
          <Moon size={13} strokeWidth={1.75} />
        </span>
      </span>
      Theme
    </button>
  );
}
