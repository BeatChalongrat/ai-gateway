"use client";

// Agent Market availability — "flag off = absent".
//
// Step 1 asks the Next server (same origin) whether the feature is switched
// on for this deployment (AGENT_MARKET_ENABLED, read at runtime — not a
// NEXT_PUBLIC_ value baked into the bundle). Only when it is on does step 2
// ask chat-edge whether this account may use it. With the flag off, chat-edge
// is never called.
//
// One answer per page load, shared by every caller (module-level promise).
// The hook is synchronous on first render ("unknown", or the answer already
// known) and fetches only inside an effect, so a surface that mounts it in a
// test without fetch — or with fetch failing — renders nothing and throws
// nothing.

import { useEffect, useState } from "react";

import { amApi } from "./api";
import type { Contact, Pricing } from "./types";

export type AgentMarketState = "unknown" | "off" | "available" | "unavailable";

export interface AgentMarketSnapshot {
  state: AgentMarketState;
  /** The runner's last heartbeat is fresh (null = not known). */
  runnerHealthy: boolean | null;
  /** AM3: this person's role in the organisation ("" = not on the list). */
  role?: string;
  admin?: boolean;
  publisher?: boolean;
  /** Whom to ask (access, budgets). */
  contact?: Contact | null;
  /** credits → baht for costs shown in the catalog. */
  pricing?: Pricing | null;
}

const UNKNOWN: AgentMarketSnapshot = { state: "unknown", runnerHealthy: null };
const OFF: AgentMarketSnapshot = { state: "off", runnerHealthy: null };

/** 3,000,000 credits = 1 USD, 35 THB per USD unless the server says otherwise. */
export const DEFAULT_PRICING: Pricing = { credits_per_usd: 3_000_000, thb_per_usd: 35 };

let pending: Promise<AgentMarketSnapshot> | null = null;
let known: AgentMarketSnapshot = UNKNOWN;

async function resolveAvailability(): Promise<AgentMarketSnapshot> {
  if (typeof fetch !== "function") return OFF;
  try {
    const res = await fetch("/api/agent-market", { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) return OFF;
    const body = (await res.json()) as { enabled?: unknown } | null;
    if (body?.enabled !== true) return OFF;
  } catch {
    return OFF;
  }
  try {
    const a = await amApi.availability();
    return {
      state: a?.available === true ? "available" : "unavailable",
      runnerHealthy: a?.runner?.healthy ?? null,
      role: typeof a?.role === "string" ? a.role : "",
      admin: a?.admin === true,
      publisher: a?.publisher === true,
      contact: a?.contact ?? null,
      pricing: a?.pricing && a.pricing.credits_per_usd > 0 ? a.pricing : null,
    };
  } catch {
    return { state: "unavailable", runnerHealthy: null };
  }
}

/** The shared availability answer (fetched once per page load). */
export function loadAgentMarket(): Promise<AgentMarketSnapshot> {
  if (!pending) {
    pending = resolveAvailability()
      .catch(() => OFF)
      .then((s) => {
        known = s;
        return s;
      });
  }
  return pending;
}

export function useAgentMarket(): AgentMarketSnapshot {
  const [snap, setSnap] = useState<AgentMarketSnapshot>(known);
  useEffect(() => {
    let alive = true;
    loadAgentMarket().then(
      (s) => {
        if (!alive) return;
        setSnap((prev) =>
          prev.state === s.state && prev.runnerHealthy === s.runnerHealthy && prev.role === s.role && prev.admin === s.admin ? prev : s,
        );
      },
      () => {},
    );
    return () => {
      alive = false;
    };
  }, []);
  return snap;
}

/** Test seam: forget the answer. */
export function resetAgentMarketForTest(): void {
  pending = null;
  known = UNKNOWN;
}

// ── "the list of my agents changed" ────────────────────────────────────
// The sidebar section and the workspace rail re-read /installs when an
// install is added or removed elsewhere on the page.

type Listener = () => void;
const listeners = new Set<Listener>();

export function onInstallsChanged(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function notifyInstallsChanged(): void {
  for (const fn of [...listeners]) {
    try {
      fn();
    } catch {
      /* a listener's failure is its own */
    }
  }
}
