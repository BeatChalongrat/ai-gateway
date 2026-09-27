"use client";

// AM3 — the left rail's data (GET /rail): my installs with pins, recent use
// and what waits for an answer, plus the person's rail preferences. It is
// re-read every 30 s while the page is visible, when the window regains
// focus and when an install changes elsewhere on the page — a run that parks
// on a question in the background must show up without a reload.

import { useCallback, useEffect, useRef, useState } from "react";

import { amApi } from "./api";
import type { RailInstall, RailPrefs } from "./types";
import { onInstallsChanged } from "./useAgentMarket";

export const RAIL_POLL_MS = 30_000;

export interface RailState {
  installs: RailInstall[];
  waitingTotal: number;
  prefs: RailPrefs;
  sharedUnopened: number;
  loaded: boolean;
  reload: () => void;
  /** Optimistic local change (a pin), then the server's answer on the next read. */
  patch: (id: string, change: Partial<RailInstall>) => void;
  setPrefs: (p: RailPrefs) => void;
}

export function useRail(enabled: boolean): RailState {
  const [installs, setInstalls] = useState<RailInstall[]>([]);
  const [waitingTotal, setWaitingTotal] = useState(0);
  const [prefs, setPrefsState] = useState<RailPrefs>({});
  const [sharedUnopened, setShared] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const alive = useRef(true);

  const load = useCallback(() => {
    amApi.rail().then(
      (r) => {
        if (!alive.current) return;
        setInstalls(r?.installs ?? []);
        setWaitingTotal(typeof r?.waiting_total === "number" ? r.waiting_total : 0);
        setPrefsState(r?.prefs ?? {});
        setShared(typeof r?.shared_unopened === "number" ? r.shared_unopened : 0);
        setLoaded(true);
      },
      () => {
        /* the rail keeps what it had */
      },
    );
  }, []);

  useEffect(() => {
    alive.current = true;
    if (!enabled) return;
    load();
    const off = onInstallsChanged(load);
    const tick = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") load();
    }, RAIL_POLL_MS);
    const onFocus = () => load();
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    if (typeof window !== "undefined") window.addEventListener("focus", onFocus);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive.current = false;
      off();
      clearInterval(tick);
      if (typeof window !== "undefined") window.removeEventListener("focus", onFocus);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, load]);

  const patch = useCallback((id: string, change: Partial<RailInstall>) => {
    setInstalls((rows) => rows.map((r) => (r.id === id ? { ...r, ...change } : r)));
  }, []);

  const setPrefs = useCallback((p: RailPrefs) => {
    setPrefsState((cur) => ({ ...cur, ...p }));
    amApi.putPrefs(p).then(
      () => {},
      () => {
        /* kept locally for this page; the next read shows what the server has */
      },
    );
  }, []);

  return { installs, waitingTotal, prefs, sharedUnopened, loaded, reload: load, patch, setPrefs };
}

/** Pin or unpin, telling the rest of the page. */
export async function togglePin(installId: string, pinned: boolean): Promise<string | null> {
  const r = pinned ? await amApi.unpin(installId) : await amApi.pin(installId);
  return r?.pinned_at ?? null;
}
