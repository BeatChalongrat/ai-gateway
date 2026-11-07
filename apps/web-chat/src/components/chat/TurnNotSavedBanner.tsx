"use client";

// FIX121 C4 — "this turn was not saved to the server" strip. The desktop host
// runs a turn itself and mirrors it to the server afterwards; when that
// mirror is refused (or the server names items it would not store) the host
// posts `host.notice` {kind:"turn-not-saved", conversationId, runId?, reason,
// status?, detail?}. Until FIX121 that failure was one line in a log file:
// the person read a normal answer while the web, their other device and the
// server-side summary never had the turn.
//
// A strip, not a toast: it has to still be there when the person looks up
// from the answer. Dismissible, non-modal. Hosted mode only — a plain browser
// never receives the notice (its turns are written by the server itself,
// before the model is called) and renders nothing.

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { bridge, type HostNotice } from "@/lib/bridge";
import { useT } from "@/lib/i18n";

type NotSaved = Extract<HostNotice, { kind: "turn-not-saved" }>;

export function TurnNotSavedBanner() {
  const hosted = useHostedMode();
  const t = useT();
  const [notice, setNotice] = useState<NotSaved | null>(null);

  useEffect(() => {
    if (!hosted) return;
    return bridge.onHostNotice((n) => {
      if (n.kind === "turn-not-saved") setNotice(n);
    });
  }, [hosted]);

  if (!hosted || !notice) return null;

  return (
    <div
      role="alert"
      data-testid="turn-not-saved-banner"
      className="mx-3 mt-1 flex shrink-0 items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-text-secondary md:mx-4"
    >
      <span className="min-w-0 flex-1">
        {t(notice.reason === "rejected" ? "turn.notSaved.rejected" : "turn.notSaved.failed")}
        {notice.runId ? <span className="ml-1 text-text-muted">{t("turn.notSaved.ref", { id: notice.runId })}</span> : null}
      </span>
      <button
        type="button"
        onClick={() => setNotice(null)}
        aria-label={t("turn.notSaved.dismiss")}
        className="focus-ring rounded-md p-1 text-text-muted transition-colors hover:bg-bg-surface hover:text-text-primary"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
