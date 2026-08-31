"use client";

// FIX34 Part A — /quick: the quick-ask window's page (desktop only). A Next
// page may export nothing but the page itself, so the component lives in
// components/quick/QuickAsk.tsx (the doc comment is there too).

import { QuickAsk } from "@/components/quick/QuickAsk";
import { useDesktopOnlyRoute } from "@/hooks/useDesktopOnlyRoute";

export default function QuickAskPage() {
  // FIX34.1 Part F — on the web this route lands on /chat, never on a wall.
  const hosted = useDesktopOnlyRoute("/chat");
  if (!hosted) return null;
  return <QuickAsk />;
}
