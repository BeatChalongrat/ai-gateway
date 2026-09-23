"use client";

// FIX130 C2 — true only when the server said a knowledge base can work
// right now (GET /knowledge-bases/availability). Every knowledge-base
// affordance outside the knowledge page — the composer's ฐานความรู้ button,
// the project panel's dropdown and hint, the sidebar and mobile menu
// entries — renders behind this, so none of them leads to a feature whose
// uploads all fail. Asks once per session.

import { useEffect } from "react";

import { useKnowledgeStore } from "@/stores/knowledgeStore";

export function useKnowledgeAvailable(): boolean {
  const availability = useKnowledgeStore((s) => s.availability);
  const load = useKnowledgeStore((s) => s.loadAvailability);
  useEffect(() => {
    void load();
  }, [load]);
  return availability === "available";
}
