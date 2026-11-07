"use client";

// FIX130 C3.1 — what a route shows when the page-load session check could
// not get an answer (network error, 5xx, or /portal/me hanging past its
// deadline). Before, that was a bare spinner forever (NIGHT2 F1: empty DOM
// at 90 s) — or, for an answer that did come back as an error, a bounce
// through the revoking logout that signed the device out for real. This
// screen says what happened, promises nothing it cannot keep, and retries
// on a click or when the browser reports the network back.

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useT } from "@/lib/i18n";

export function SessionUnreachable({ onRetry, checking = false }: { onRetry: () => void; checking?: boolean }) {
  const t = useT();
  useEffect(() => {
    const onOnline = () => onRetry();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [onRetry]);
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-base px-4">
      <div role="alert" data-testid="session-unreachable" className="w-full max-w-sm rounded-xl border border-border bg-bg-surface p-5 text-center shadow-sm">
        <h1 className="text-base font-semibold text-text-primary">{t("session.unreachable.title")}</h1>
        <p className="mt-2 text-sm text-text-secondary">{t("session.unreachable.body")}</p>
        <Button data-testid="session-retry" className="mt-4" loading={checking} onClick={onRetry}>
          {checking ? t("session.unreachable.checking") : t("session.unreachable.retry")}
        </Button>
      </div>
    </div>
  );
}

/** FIX132 N9 — the wait before the session check answers. It used to be a
 *  lone spinner on white for up to 8 s (NIGHT3 f1-blank-3s): now the brand
 *  shows at once and, after a moment, a line says what is happening (in a
 *  live region that is mounted empty, so the line is announced). */
export function SessionChecking({ delayMs = 1500 }: { delayMs?: number }) {
  const t = useT();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), delayMs);
    return () => window.clearTimeout(id);
  }, [delayMs]);
  return (
    <div data-testid="session-checking" className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg-base px-4 text-text-secondary">
      {/* The same mark the new-chat screen shows; decorative here. */}
      <img src="/sran-logo.png" alt="" aria-hidden className="sran-logo h-16 w-auto select-none" draggable={false} />
      <Spinner size={22} />
      <p role="status" aria-live="polite" className="min-h-[1.25rem] text-sm">
        {slow ? t("session.checking") : ""}
      </p>
    </div>
  );
}
