"use client";

// useMemoryPendingCount — shared hook backing the S8 sidebar
// Memory badge + the MemoryIndicator dot. Polls every 2 minutes
// (matches the extractor's "once-per-conversation" cadence — no
// point hitting the endpoint faster than new suggestions can
// surface) AND exposes a refresh() escape hatch so the Memory
// settings page can decrement the badge the instant the user
// confirms or rejects a suggestion.
//
// Single zustand store so multiple consumers don't poll twice.

import { useEffect } from "react";

import { create } from "zustand";

import { api } from "@/lib/api";

interface State {
	count: number;
	loading: boolean;
	loadedAt: number;
	refresh: () => Promise<void>;
}

const POLL_INTERVAL_MS = 2 * 60 * 1000;

export const useMemoryPendingStore = create<State>((set) => ({
	count: 0,
	loading: false,
	loadedAt: 0,
	async refresh() {
		set({ loading: true });
		try {
			const res = await api.getMemoryPendingCount();
			set({ count: res.count ?? 0, loading: false, loadedAt: Date.now() });
		} catch {
			// Silent — the badge degrades gracefully.
			set({ loading: false, loadedAt: Date.now() });
		}
	},
}));

// useMemoryPendingPoll is the side-effect hook a single mount-
// point in the app calls (the chat layout) to kick the poll
// loop. Other consumers read useMemoryPendingStore.count directly.
//
// Keeping the poll trigger separate from the read keeps the
// Sidebar + MemoryIndicator props minimal — they just subscribe
// to the count, they don't manage the timer.
export function useMemoryPendingPoll() {
	const refresh = useMemoryPendingStore((s) => s.refresh);
	useEffect(() => {
		void refresh();
		const id = window.setInterval(() => {
			void refresh();
		}, POLL_INTERVAL_MS);
		return () => window.clearInterval(id);
	}, [refresh]);
}
