"use client";

// FIX22 §2 — update NOTIFICATION banner (stage 1). The desktop host posts a
// `host.notice` {kind:"update-available", latest, current} when the gateway
// publishes a version newer than the running app. This renders a small,
// dismissible, non-modal strip. There is deliberately NO link, NO download
// and NO install action: the user installs by hand (stage 2 — a signed
// update feed — waits for a code-signing certificate). Dismissal is remembered
// PER VERSION in localStorage, so a dismissed version stays dismissed across
// reloads but the next version shows again. Hosted mode only: a plain browser
// never receives the notice and renders nothing.

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { bridge, type HostNotice } from "@/lib/bridge";
import { useT } from "@/lib/i18n";

export const UPDATE_DISMISSED_KEY = "sran.desktop.updateDismissed";

function readDismissed(): string {
  try {
    return window.localStorage.getItem(UPDATE_DISMISSED_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeDismissed(version: string): void {
  try {
    window.localStorage.setItem(UPDATE_DISMISSED_KEY, version);
  } catch {
    /* private mode / blocked storage — the banner just returns next reload */
  }
}

export function UpdateBanner() {
  const hosted = useHostedMode();
  const t = useT();
  const [notice, setNotice] = useState<Extract<HostNotice, { kind: "update-available" }> | null>(null);
  const [dismissedVersion, setDismissedVersion] = useState<string>("");

  useEffect(() => {
    if (!hosted) return;
    setDismissedVersion(readDismissed());
    return bridge.onHostNotice((n) => {
      if (n.kind === "update-available") setNotice(n);
    });
  }, [hosted]);

  if (!hosted || !notice || notice.latest === dismissedVersion) return null;

  const dismiss = () => {
    writeDismissed(notice.latest);
    setDismissedVersion(notice.latest);
  };

  return (
    <div
      role="status"
      data-testid="update-banner"
      className="mx-3 mt-1 flex shrink-0 items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2 text-xs text-text-secondary md:mx-4"
    >
      <span className="min-w-0 flex-1">
        {t("update.available", { latest: notice.latest, current: notice.current })}{" "}
        <span className="text-text-tertiary">{t("update.howTo")}</span>
      </span>
      <button
        type="button"
        onClick={dismiss}
        aria-label={t("update.desktopDismiss")}
        className="focus-ring rounded-md p-1 text-text-tertiary transition-colors hover:bg-bg-surface hover:text-text-primary"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
