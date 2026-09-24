"use client";

// FIX132 C — the server's data policy, asked once per page load and shared
// by every caller (the Settings "your data" section, the delete-chat
// confirmations). Until it answers — or when it cannot — the defaults the
// server itself uses are shown.

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { chatDeletionNotice, DEFAULT_DATA_POLICY, type DataPolicy } from "@/lib/dataPolicy";
import type { DictKey } from "@/lib/i18n/dict";

let cached: DataPolicy | null = null;
let inflight: Promise<DataPolicy> | null = null;

export function loadDataPolicy(): Promise<DataPolicy> {
  if (cached) return Promise.resolve(cached);
  if (!inflight) {
    inflight = api
      .getDataPolicy()
      .then((p) => {
        cached = { ...DEFAULT_DATA_POLICY, ...p };
        return cached;
      })
      .catch(() => DEFAULT_DATA_POLICY)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** `enabled` false = do not ask yet (a dialog that is closed). */
export function useDataPolicy(enabled = true): DataPolicy {
  const [policy, setPolicy] = useState<DataPolicy>(cached ?? DEFAULT_DATA_POLICY);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void loadDataPolicy().then((p) => {
      if (live) setPolicy(p);
    });
    return () => {
      live = false;
    };
  }, [enabled]);
  return policy;
}

/** Test seam: forget the cached answer. */
export function resetDataPolicyForTest(): void {
  cached = null;
  inflight = null;
}

/** The delete-chat confirmation sentence, with the real period. */
export function useChatDeletionText(
  t: (key: DictKey, vars?: Record<string, string>) => string,
  keys: { on: DictKey; kept: DictKey },
  enabled = true,
): string {
  const policy = useDataPolicy(enabled);
  const n = chatDeletionNotice(policy, keys);
  return t(n.key, n.vars);
}
