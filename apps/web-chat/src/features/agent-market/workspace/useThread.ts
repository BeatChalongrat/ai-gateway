"use client";

// The agent workspace thread: load everything, then follow the event stream.
//
//   GET /installs/{id}/thread?after=0   (paged by the server at 500 rows)
//   GET /installs/{id}/events?after=N   (SSE: `event: item`, `event: reconnect`)
//
// The stream is resumed from the highest rev seen: after the server's own
// 10-minute close (`reconnect`), after the connection ends, or after 45 s
// of total silence (the server sends a keepalive every 15 s, so silence
// means a dead socket). Backoff 1 s → 5 s between attempts.

import { useCallback, useEffect, useRef, useState } from "react";

import { parseSSE } from "@/lib/stream";

import { amApi } from "../api";
import { maxRev, mergeItems } from "../thread";
import type { ThreadItem } from "../types";

const SILENCE_MS = 45_000;
const PAGE = 500;

export type ThreadStatus = "loading" | "live" | "reconnecting" | "error";

export interface ThreadState {
  items: ThreadItem[];
  status: ThreadStatus;
  error: unknown;
  /** Merge items obtained elsewhere (a POST response) — same rules as the stream. */
  merge: (items: ThreadItem[]) => void;
  retry: () => void;
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const id = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        resolve();
      },
      { once: true },
    );
  });

/** AM4: threadId = the conversation on screen (null = the install's first). */
export function useThread(installId: string | null, threadId: string | null = null): ThreadState {
  const [items, setItems] = useState<ThreadItem[]>([]);
  const [status, setStatus] = useState<ThreadStatus>("loading");
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const cursorRef = useRef(0);

  const merge = useCallback((incoming: ThreadItem[]) => {
    if (!incoming.length) return;
    setItems((cur) => {
      const next = mergeItems(cur, incoming);
      cursorRef.current = Math.max(cursorRef.current, maxRev(incoming));
      return next;
    });
  }, []);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (!installId) return;
    const stop = new AbortController();
    setItems([]);
    setStatus("loading");
    setError(null);
    cursorRef.current = 0;

    const run = async () => {
      // 1 — the whole thread, page by page.
      try {
        let after = 0;
        for (;;) {
          const page = await amApi.thread(installId, after, threadId);
          if (stop.signal.aborted) return;
          const got = page?.items ?? [];
          merge(got);
          after = Math.max(after, page?.cursor ?? 0, maxRev(got));
          cursorRef.current = Math.max(cursorRef.current, after);
          if (got.length < PAGE) break;
        }
      } catch (err) {
        if (stop.signal.aborted) return;
        setError(err);
        setStatus("error");
        return;
      }
      setStatus("live");

      // 2 — follow the stream.
      let backoff = 1000;
      while (!stop.signal.aborted) {
        const conn = new AbortController();
        const dead = new AbortController();
        const onStop = () => conn.abort();
        stop.signal.addEventListener("abort", onStop, { once: true });
        let timer: ReturnType<typeof setTimeout> | undefined;
        const arm = () => {
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => dead.abort(), SILENCE_MS);
        };
        // The server's own 10-minute close is planned: reattach quietly.
        let planned = false;
        try {
          arm();
          const res = await amApi.events(installId, cursorRef.current, conn.signal, threadId);
          if (res.status === 401 || res.status === 403 || res.status === 404) {
            setError(new Error(`events ${res.status}`));
            setStatus("error");
            return;
          }
          if (!res.ok) throw new Error(`events ${res.status}`);
          setStatus("live");
          for await (const evt of parseSSE<unknown>(res, conn.signal, arm, dead.signal)) {
            if (evt.event === "item" && evt.data && typeof evt.data === "object") {
              merge([evt.data as ThreadItem]);
              backoff = 1000;
            } else if (evt.event === "reconnect") {
              const after = (evt.data as { after?: number } | null)?.after;
              if (typeof after === "number") cursorRef.current = Math.max(cursorRef.current, after);
              planned = true;
              break;
            }
          }
        } catch {
          /* a dropped or silent connection: reattach below */
        } finally {
          if (timer) clearTimeout(timer);
          stop.signal.removeEventListener("abort", onStop);
          conn.abort();
        }
        if (stop.signal.aborted) return;
        if (planned) {
          backoff = 1000;
          await sleep(250, stop.signal);
          continue;
        }
        setStatus("reconnecting");
        await sleep(backoff, stop.signal);
        backoff = Math.min(backoff * 2, 5000);
      }
    };
    void run();
    return () => stop.abort();
  }, [installId, threadId, attempt, merge]);

  return { items, status, error, merge, retry };
}
