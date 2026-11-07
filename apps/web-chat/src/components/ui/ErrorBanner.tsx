"use client";

// The store's error banner (bottom-right). Lifted out of AppProviders
// (FIX86) so it can sit ABOVE the composer like every other
// bottom-floating element — it used to be `fixed bottom-6`, a literal
// that put it on top of the composer in the 460 px quick window
// (FIX83 row 17).
//
// FIX127 A3 — when a send was refused because its conversation had been
// deleted elsewhere, the banner carries one action: send the kept text in a
// new chat. The landing page performs it (it owns the router and the
// create-then-send flow); the banner only announces the click.

import { PILL_GAP, useBottomInset } from "@/hooks/useBottomInset";
import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

/** DOM event the landing page listens for. */
export const SEND_STRANDED_EVENT = "sran:send-stranded";

export function ErrorBanner({ message }: { message: string | null }) {
  const inset = useBottomInset();
  const t = useT();
  const stranded = useChatStore((s) => s.strandedSend);
  if (!message) return null;
  return (
    <div
      role="alert"
      data-testid="error-banner"
      className="fixed right-6 z-50 max-w-sm rounded-md border border-status-danger/40 bg-status-danger/15 px-4 py-3 text-sm text-text-primary shadow-lg fade-in-up"
      style={{ bottom: inset + PILL_GAP }}
    >
      {message}
      {stranded ? (
        <button
          type="button"
          data-testid="send-in-new-chat"
          className="focus-ring mt-2 block rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-bg-base hover:opacity-90"
          onClick={() => window.dispatchEvent(new CustomEvent(SEND_STRANDED_EVENT))}
        >
          {t("error.sendInNewChat")}
        </button>
      ) : null}
    </div>
  );
}
