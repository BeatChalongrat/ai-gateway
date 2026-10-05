"use client";

// AM10 F — the language a person chose is their account's, not one browser's.
//
// The choice used to live in this browser's storage alone: switch to Thai in
// the browser and the desktop app stayed in English, and the other way round.
// It is now kept on the person's account as well (the gateway's
// /portal/me `preferred_language`, which the portal's settings page and the
// transactional e-mails already use), and every client follows it:
//
//   * a person switches language here            → saved to the account;
//   * this client's choice differs from the      → the account's wins, and the
//     account's, and this client's was saved        page changes at once (on
//     before (so the other client changed it)       load, and when it regains
//                                                   focus);
//   * this client has a choice the account has   → it is saved now (a choice
//     never been told (made before this, or         is never lost to a failed
//     while the gateway could not be reached)       request);
//   * this client has no choice yet              → the account's "th" is taken;
//     "en" is also what an account that never chose says, so then the
//     browser's own language stands until the person chooses.
//
// Nothing here can throw or block: a gateway that does not answer leaves the
// page in the language it is in.

import { GATEWAY_URL } from "@/lib/auth";

import type { Locale } from "./dict";
import { onLocaleChosen, useI18nStore } from "./index";

const STORAGE_KEY = "sran-chat-locale";
// "1": the account holds this client's choice · "pending": a choice it has not taken yet
const SYNC_KEY = "sran-chat-locale-synced";
const RECHECK_MS = 60_000;

const isLocale = (v: unknown): v is Locale => v === "en" || v === "th";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* a browser without storage follows the account on every load */
  }
}

/** Save a choice to the account. "pending" until the gateway took it. */
export async function saveLanguage(locale: Locale): Promise<boolean> {
  write(SYNC_KEY, "pending");
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/me`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ preferred_language: locale }),
    });
    if (!res.ok) return false;
    // a later choice may have been made while this one was on its way
    if (read(STORAGE_KEY) === locale) write(SYNC_KEY, "1");
    return true;
  } catch {
    return false;
  }
}

/** What the account says, seen at a session check: apply the rules above. */
export function accountLanguageSeen(server: string | null | undefined): void {
  if (typeof window === "undefined" || !isLocale(server)) return;
  const local = read(STORAGE_KEY);
  const synced = read(SYNC_KEY) === "1";
  if (isLocale(local)) {
    if (!synced) {
      if (local === server) write(SYNC_KEY, "1");
      else void saveLanguage(local);
      return;
    }
    if (local !== server) useI18nStore.getState().adopt(server);
    return;
  }
  if (server === "th") {
    useI18nStore.getState().adopt("th");
    write(SYNC_KEY, "1");
  }
}

let lastCheck = 0;

/** Read the account's language again (the other client may have changed it): at most once a minute. */
export async function recheckAccountLanguage(now: number = Date.now()): Promise<void> {
  if (now - lastCheck < RECHECK_MS) return;
  lastCheck = now;
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/me`, { credentials: "include", cache: "no-store" });
    if (!res.ok) return;
    const me = (await res.json()) as { preferred_language?: string | null };
    accountLanguageSeen(me?.preferred_language);
  } catch {
    /* the next focus tries again */
  }
}

/** Tests only. */
export function resetAccountLanguageForTest(): void {
  lastCheck = 0;
}

// A person's own choice goes to their account; coming back to the page asks
// the account whether the other client changed it.
onLocaleChosen((l) => void saveLanguage(l));
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void recheckAccountLanguage();
  });
}
