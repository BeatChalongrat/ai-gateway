"use client";

// FIX130 C4 — what a page stacks directly on top of its composer (the
// phone's long-chat chip, the temporary-chat strip, the message queue),
// measured while it is mounted, so the credits badge floats above it rather
// than on it (NIGHT2 F11: the badge sat on the memory strip at 360 px).
// A component rather than a ref on the page: the page renders a skeleton
// first, and the stack only exists once the conversation has loaded.

import { useRef } from "react";

import { useAboveComposerProbe } from "@/hooks/useBottomInset";

export function AboveComposerStack({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useAboveComposerProbe(ref);
  return (
    <div ref={ref} data-testid="above-composer">
      {children}
    </div>
  );
}
