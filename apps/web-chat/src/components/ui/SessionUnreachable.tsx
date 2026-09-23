"use client";

// FIX130 C3.1 — what a route shows when the page-load session check could
// not get an answer (network error, 5xx, or /portal/me hanging past its
// deadline). Before, that was a bare spinner forever (NIGHT2 F1: empty DOM
// at 90 s) — or, for an answer that did come back as an error, a bounce
// through the revoking logout that signed the device out for real. This
// screen says what happened, promises nothing it cannot keep, and retries
// on a click or when the browser reports the network back.

import { useEffect } from "react";

import { Button } from "@/components/ui/Button";
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
