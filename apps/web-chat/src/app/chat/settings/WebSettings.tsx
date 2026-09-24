"use client";

// Web "Settings" — the browser half of /chat/settings (FIX43 Part B4).
//
// Deliberately small, and shaped like the big products' settings pages:
// Appearance (theme, language), Account (who you are, log out), Data (the
// account-wide pages that live elsewhere — Memory, Knowledge base, Skills),
// Your data (FIX132: export everything, delete the account, and what
// deleting does when).
// Nothing here is device state: theme and language are the same stores the
// sidebar menu already drives, and the account rows go through the same
// session helpers the sidebar uses. Connectors and Writing style are NOT
// listed — on the web they do not exist (their state lives in the desktop
// host; see docs/FIX43_REPORT.md §B2), and a door to a missing room is worse
// than no door.
//
// Every visible string comes from lib/i18n/dict.ts (settings.web.* keys).

import Link from "next/link";

import { useAuth } from "@/hooks/useAuth";
import { logoutSession } from "@/lib/auth";
import { useI18nStore, useT } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n/dict";
import { useThemeStore, type Theme } from "@/stores/themeStore";

import { BackIcon, Row, Section } from "./DeviceSettings";
import { YourDataSection } from "./YourDataSection";

export function WebSettings() {
  const t = useT();
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const locale = useI18nStore((s) => s.locale);
  const setLocale = useI18nStore((s) => s.setLocale);
  const { user } = useAuth();

  const selectClass =
    "focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary";

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base" data-testid="web-settings">
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackIcon /> {t("settings.back")}
        </Link>

        <h1 className="mt-6 text-2xl font-semibold tracking-tight text-text-primary">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-text-secondary">{t("settings.web.subtitle")}</p>

        <div className="mt-8 flex flex-col gap-8">
          <Section title={t("settings.section.appearance")}>
            <Row label={t("settings.theme")}>
              <select
                aria-label={t("settings.theme")}
                value={theme}
                onChange={(e) => setTheme(e.target.value as Theme)}
                className={selectClass}
                data-testid="web-settings-theme"
              >
                <option value="dark">{t("settings.theme.dark")}</option>
                <option value="light">{t("settings.theme.light")}</option>
              </select>
            </Row>
            <Row label={t("settings.web.language")}>
              <select
                aria-label={t("settings.web.language")}
                value={locale}
                onChange={(e) => setLocale(e.target.value as Locale)}
                className={selectClass}
                data-testid="web-settings-language"
              >
                <option value="en">{t("settings.web.language.en")}</option>
                <option value="th">{t("settings.web.language.th")}</option>
              </select>
            </Row>
          </Section>

          <Section title={t("settings.web.section.account")}>
            <Row label={t("settings.web.email")}>
              <span className="truncate text-sm text-text-secondary" data-testid="web-settings-email">
                {user?.email ?? "—"}
              </span>
            </Row>
            <Row label={t("settings.web.logout")} hint={t("settings.web.logout.hint")}>
              <button
                type="button"
                onClick={() => void logoutSession()}
                className="focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-status-danger transition-colors hover:bg-bg-elevated"
              >
                {t("menu.logout")}
              </button>
            </Row>
          </Section>

          <Section title={t("settings.web.section.data")} hint={t("settings.web.section.data.hint")}>
            <DoorRow href="/chat/memory" label={t("menu.memory")} hint={t("settings.web.memory.hint")} />
            <DoorRow href="/chat/knowledge" label={t("menu.knowledgeBase")} hint={t("settings.web.knowledge.hint")} />
            <DoorRow href="/chat/skills" label={t("menu.skills")} hint={t("settings.web.skills.hint")} />
          </Section>

          {/* FIX132 C — export all my data, delete a chat / the account, and when. */}
          <YourDataSection />
        </div>
      </main>
    </div>
  );
}

function DoorRow({ href, label, hint }: { href: string; label: string; hint: string }) {
  const t = useT();
  return (
    <Row label={label} hint={hint}>
      <Link
        href={href}
        className="focus-ring inline-flex items-center gap-1 rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary transition-colors hover:bg-bg-elevated"
      >
        {t("settings.web.open")}
        <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M9 6l6 6-6 6" />
        </svg>
      </Link>
    </Row>
  );
}
