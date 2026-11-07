"use client";

// AM3 — the left rail's data (GET /rail): my installs with pins, recent use
// and what waits for an answer, plus the person's rail preferences. It is
// re-read every 30 s while the page is visible, when the window regains
// focus and when an install changes elsewhere on the page — a run that parks
// on a question in the background must show up without a reload.
// AM24c — a read refreshes what the rows say, never where they are: the
// sidebar's order is settled once a page and held (useHeldOrder, rail.ts settleRail).

import { useCallback, useEffect, useRef, useState } from "react";

import { amApi } from "./api";
import { placePins, sameHold, settleRail, type RailHold } from "./rail";
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

/** Pin or unpin, telling the rest of the page — the sidebar moves that row (AM24c: a pin is asked for). */
export async function togglePin(installId: string, pinned: boolean): Promise<string | null> {
  const r = pinned ? await amApi.unpin(installId) : await amApi.pin(installId);
  const at = r?.pinned_at ?? null;
  for (const fn of [...pinListeners]) {
    try {
      fn(installId, at);
    } catch {
      /* a listener's failure is its own */
    }
  }
  return at;
}

type PinListener = (installId: string, pinnedAt: string | null) => void;
const pinListeners = new Set<PinListener>();

function onPinChanged(fn: PinListener): () => void {
  pinListeners.add(fn);
  return () => {
    pinListeners.delete(fn);
  };
}

// ── AM24c: the sidebar's order, held for the page's life ──────────────────
// Kept in the page's own memory, not in any storage: the rail unmounting and
// mounting again keeps it, and a reload or any full-page load — the desktop
// app makes some of its own (File > New chat, a deep link, a notification,
// signing in again) — arranges the agents by recency again.
let heldInPage: RailHold | null = null;

/** Test seam: a new page. */
export function forgetHeldOrderForTest(): void {
  heldInPage = null;
}

/**
 * The sidebar's order: settled at the page's first read, then held through every poll and focus for the page's life.
 * `pinned` re-places a row the person pinned or unpinned (the rail's own pin button; a pin made elsewhere on the
 * page arrives through togglePin).
 */
export function useHeldOrder(
  rows: RailInstall[],
  loaded: boolean,
  activeId: string | null,
): { hold: RailHold | null; pinned: (id: string, pinnedAt: string | null) => void } {
  const [hold, setHold] = useState<RailHold | null>(null);
  const holdRef = useRef<RailHold | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  const keep = useCallback((h: RailHold) => {
    if (sameHold(holdRef.current, h)) return;
    holdRef.current = h;
    heldInPage = h;
    setHold(h);
  }, []);

  useEffect(() => {
    if (loaded) keep(settleRail(rows, holdRef.current ?? heldInPage, activeId));
  }, [rows, loaded, activeId, keep]);

  const pinned = useCallback(
    (id: string, pinnedAt: string | null) => {
      const h = holdRef.current;
      if (h) keep(placePins(h, rowsRef.current.map((r) => (r.id === id ? { ...r, pinned_at: pinnedAt } : r)), id));
    },
    [keep],
  );
  useEffect(() => onPinChanged(pinned), [pinned]);

  return { hold, pinned };
}
