"use client";

// The store's error banner (bottom-right). Lifted out of AppProviders
// (FIX86) so it can sit ABOVE the composer like every other
// bottom-floating element — it used to be `fixed bottom-6`, a literal
// that put it on top of the composer in the 460 px quick window
// (FIX83 row 17).

import { PILL_GAP, useBottomInset } from "@/hooks/useBottomInset";

export function ErrorBanner({ message }: { message: string | null }) {
  const inset = useBottomInset();
  if (!message) return null;
  return (
    <div
      role="alert"
      data-testid="error-banner"
      className="fixed right-6 z-50 max-w-sm rounded-md border border-status-danger/40 bg-status-danger/15 px-4 py-3 text-sm text-text-primary shadow-lg fade-in-up"
      style={{ bottom: inset + PILL_GAP }}
    >
      {message}
    </div>
  );
}
