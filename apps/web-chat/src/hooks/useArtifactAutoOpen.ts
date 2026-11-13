"use client";
import { useEffect, useRef } from "react";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import type { Message } from "@/lib/types";

/** Historic messages never steal focus. Only newly delivered assistant files open. */
export function useArtifactAutoOpen(conversation: string | null, messages: Message[] | undefined, loaded: boolean) {
  const seen = useRef<{ conversation: string | null; ids: Set<string>; initialized: boolean; floor: number }>({ conversation: null, ids: new Set(), initialized: false, floor: 0 });
  const completed = useRef(new Set<string>());
  useEffect(() => {
    if (seen.current.conversation !== conversation) { seen.current = { conversation, ids: new Set(), initialized: false, floor: 0 }; completed.current.clear(); }
    if (!loaded || !messages) return;
    const deliveries = messages.filter(m => m.role === "assistant" && !m.isStreaming).flatMap(m => (m.files ?? []).map(file => ({ file, key: `${m.id}:${file.id}`, time: Date.parse(m.created_at) || 0 })));
    const fresh = deliveries.filter(d => !seen.current.ids.has(d.key) && d.time >= seen.current.floor).map(d => d.file);
    const finished = messages.filter(m => m.role === "assistant" && !m.isStreaming && m.status === "completed");
    const newTurn = finished.some(m => !completed.current.has(m.id) && (Date.parse(m.created_at) || 0) >= seen.current.floor);
    finished.forEach(m => completed.current.add(m.id));
    deliveries.forEach(d => seen.current.ids.add(d.key));
    if (!seen.current.initialized) { seen.current.initialized = true; seen.current.floor = Math.max(0, ...messages.map(m => Date.parse(m.created_at) || 0)); return; }
    const store = useSidePaneStore.getState(); fresh.forEach(store.rememberFile);
    if (newTurn) window.dispatchEvent(new CustomEvent("sran-artifact-delivered", { detail: "*" }));
    // A new agent turn can propose changes to an already-open original file.
    fresh.forEach(file => window.dispatchEvent(new CustomEvent("sran-artifact-delivered", { detail: file.id })));
    // Existing editors can contain unsaved input. Add a tab without replacing them.
    if (fresh.length && !store.pane) store.open({ kind: "preview", file: fresh[fresh.length - 1] });
  }, [conversation, messages, loaded]);
}
