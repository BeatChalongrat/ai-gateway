"use client";

// EN / ไทย switch. Styled to sit next to <ThemeToggle /> in the page
// headers, and mounted in the sidebar footer too so it is reachable from
// every screen (page headers are per-page and a few pages have none).
//
// Same hydration guard as ThemeToggle: the stored locale is only known
// after the mount effect, so render a fixed-size placeholder until then
// rather than flashing "EN" on a Thai user's first paint.

import { useEffect, useState } from "react";

import { useI18n } from "../lib/i18n";

export function LanguageToggle() {
  const { locale, toggle, t } = useI18n();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const label = locale === "en" ? "ไทย" : "EN";

  return (
    <button
      onClick={toggle}
      type="button"
      aria-label={t("app.language")}
      title={t("app.language")}
      style={{
        height: 30,
        minWidth: 38,
        padding: "0 8px",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 8,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-default)",
        color: "var(--text-secondary)",
        cursor: "pointer",
        fontSize: 12,
        fontWeight: 600,
        lineHeight: 1,
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
      {mounted ? label : <span style={{ width: 22 }} aria-hidden />}
    </button>
  );
}
