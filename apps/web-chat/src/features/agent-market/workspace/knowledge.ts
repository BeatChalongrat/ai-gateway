"use client";

// AM6 — the knowledge base a person asks an agent's questions with. It is
// the person's choice per agent, kept in this browser: an agent has no
// conversation row to bind it to, and the choice only ever travels with a
// question (a message without files). It is never the source of a number in
// a deliverable; that stays with the files and the scripts.

import { useCallback, useEffect, useState } from "react";

import { useKnowledgeStore } from "@/stores/knowledgeStore";

/** Where the choice for one agent is kept. */
export function knowledgeKeyFor(installId: string): string {
  return `am.kb.${installId}`;
}

// Storage can be off (a private window, a locked-down desktop): the page
// works without it, the choice then lasts as long as the page.
function read(installId: string): string | null {
  try {
    return window.localStorage.getItem(knowledgeKeyFor(installId)) || null;
  } catch {
    return null;
  }
}

function write(installId: string, kbId: string | null): void {
  try {
    if (kbId) window.localStorage.setItem(knowledgeKeyFor(installId), kbId);
    else window.localStorage.removeItem(knowledgeKeyFor(installId));
  } catch {
    /* storage is off */
  }
}

export function useAgentKnowledge(installId: string): [string | null, (kbId: string | null) => Promise<void>] {
  const [kbId, setKbId] = useState<string | null>(() => read(installId));
  useEffect(() => {
    setKbId(read(installId));
  }, [installId]);

  const pick = useCallback(
    async (next: string | null) => {
      setKbId(next);
      write(installId, next);
    },
    [installId],
  );

  // A knowledge base that was deleted since it was chosen is forgotten, so
  // that a question never names one that no longer exists.
  const loaded = useKnowledgeStore((s) => s.loaded && !s.error);
  const known = useKnowledgeStore((s) => (kbId ? s.knowledgeBases.some((k) => k.id === kbId) : true));
  useEffect(() => {
    if (kbId && loaded && !known) void pick(null);
  }, [kbId, loaded, known, pick]);

  return [kbId, pick];
}
