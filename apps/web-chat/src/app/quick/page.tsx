"use client";

// FIX34 Part A — /quick: the quick-ask window's page (desktop only). A Next
// page may export nothing but the page itself, so the component lives in
// components/quick/QuickAsk.tsx (the doc comment is there too).

import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { QuickAsk } from "@/components/quick/QuickAsk";
import { useDesktopOnlyRoute } from "@/hooks/useDesktopOnlyRoute";

export default function QuickAskPage() {
  // FIX34.1 Part F — on the web this route lands on /chat, never on a wall.
  const hosted = useDesktopOnlyRoute("/chat");
  if (!hosted) return null;
  return (
    <>
      <QuickAsk />
      {/* FIX81 — the quick window never mounted a canvas host, so "Open in
          Canvas" (and now the interactive-page card) silently no-opped
          here: openCanvas set store state nothing rendered. In this small
          window the panel takes its below-lg shape (FIX76): an honest
          slide-over dialog. */}
      <CanvasPanel />
    </>
  );
}
