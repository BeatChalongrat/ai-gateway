"use client";

// Phase 6 / C3 — tells a CodeBlock which conversation + message it is
// rendered inside, so "Open in Canvas" can bind the artifact to a
// persisted lineage (edits + versions survive a reload).
//
// A context is used instead of prop-drilling because the code block is
// created deep inside react-markdown's component map, which we must
// not restructure (the P1 render pipeline is load-bearing).
//
// Outside a chat message (e.g. the public /shared view) the context is
// absent → the canvas still opens, just in ad-hoc mode with no
// persistence. Nothing crashes and no request is made.

import { createContext, useContext, useMemo } from "react";

export interface CanvasSource {
  conversationId: string;
  messageId: string;
}

const Ctx = createContext<CanvasSource | null>(null);

export function CanvasSourceProvider({
  conversationId,
  messageId,
  children,
}: CanvasSource & { children: React.ReactNode }) {
  const value = useMemo(
    () => ({ conversationId, messageId }),
    [conversationId, messageId],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCanvasSource(): CanvasSource | null {
  return useContext(Ctx);
}

/** Stable identity for one artifact lineage. Derived from the message
 *  id + a hash of the ORIGINAL block source, so reopening the same
 *  block later (after edits, after a reload) resolves to the same
 *  lineage — while two different blocks in one message stay distinct. */
export function artifactKeyFor(messageId: string | undefined, content: string): string {
  let h = 5381;
  for (let i = 0; i < content.length; i++) {
    h = ((h << 5) + h + content.charCodeAt(i)) | 0;
  }
  return `${messageId ?? "adhoc"}:${(h >>> 0).toString(36)}`;
}
