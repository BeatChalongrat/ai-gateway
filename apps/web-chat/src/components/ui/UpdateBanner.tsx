"use client";

// "New version available" prompt. A persistent pill (same visual family
// as the offline indicator / error banner) rather than the ephemeral
// Toast — an update prompt needs a button and must not auto-dismiss
// after 2.5s. Never reloads on its own; the user clicks refresh, or
// dismisses for the rest of this page load (the hook latches once per
// build, so a dismissed banner stays gone until the next reload).

import { useState } from "react";
import { useVersionCheck } from "@/hooks/useVersionCheck";
import { useT } from "@/lib/i18n";

export function UpdateBanner() {
  const updateAvailable = useVersionCheck();
  const [dismissed, setDismissed] = useState(false);
  const t = useT();
  if (!updateAvailable || dismissed) return null;
  return (
    <div
      role="status"
      className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-full border border-border bg-bg-surface px-4 py-2 text-sm text-text-primary shadow-lg"
    >
      <span>{t("update.newVersion")}</span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="focus-ring rounded-full bg-accent px-3 py-1 text-xs font-medium text-white hover:opacity-90"
      >
        {t("update.refresh")}
      </button>
      <button
        type="button"
        aria-label={t("update.dismiss")}
        onClick={() => setDismissed(true)}
        className="focus-ring rounded-full px-1 text-xs text-text-secondary hover:text-text-primary"
      >
        ✕
      </button>
    </div>
  );
}
