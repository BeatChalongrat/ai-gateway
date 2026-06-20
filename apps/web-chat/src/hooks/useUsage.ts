"use client";

// Usage & credits data hook (credit badge + popover).
//
// react-query owns caching: staleTime keeps popover opens from
// refetching more than once a minute, while bumpUsageVersion() —
// called by the chat store after each completed assistant turn —
// invalidates the query so the badge tracks spend without polling.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { create } from "zustand";

import { api } from "@/lib/api";

// Tiny version counter bridging the (non-react-query) chat store to
// the query cache: message_complete bumps it, the hook invalidates.
interface UsageVersionState {
  version: number;
  bump: () => void;
}

export const useUsageVersionStore = create<UsageVersionState>((set) => ({
  version: 0,
  bump: () => set((s) => ({ version: s.version + 1 })),
}));

/** Call after a completed assistant turn so the credit badge refreshes. */
export function bumpUsageVersion(): void {
  useUsageVersionStore.getState().bump();
}

export function useUsage() {
  const version = useUsageVersionStore((s) => s.version);
  const queryClient = useQueryClient();

  // A bumped version marks the cached data stale and refetches in the
  // background — the badge updates shortly after each reply finishes.
  useEffect(() => {
    if (version > 0) {
      void queryClient.invalidateQueries({ queryKey: ["usage"] });
    }
  }, [version, queryClient]);

  return useQuery({
    queryKey: ["usage"],
    queryFn: () => api.getUsage(),
    staleTime: 60_000,
    refetchOnMount: false,
  });
}
