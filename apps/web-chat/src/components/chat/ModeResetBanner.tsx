"use client";

// FIX25 §1c.3 — "your permission mode was reset" strip. The desktop host posts
// ONE `host.notice` {kind:"approval-mode-reset", from, to} at start when the
// stored mode was `skip` (skip never persists across restarts). Dismissible,
// non-modal, shown once per launch (the host does not repeat it). Hosted mode
// only: a plain browser never receives it and renders nothing.

import { useEffect, useState } from "react";
import { X } from "lucide-react";

import { useHostedMode } from "@/hooks/useHostedMode";
import { bridge, type HostNotice } from "@/lib/bridge";
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

const LABEL: Record<string, DictKey> = { manual: "perm.mode.manual", auto: "perm.mode.auto", skip: "perm.mode.skip" };

export function ModeResetBanner() {
  const hosted = useHostedMode();
  const t = useT();
  const [notice, setNotice] = useState<Extract<HostNotice, { kind: "approval-mode-reset" }> | null>(null);

  useEffect(() => {
    if (!hosted) return;
    return bridge.onHostNotice((n) => {
      if (n.kind === "approval-mode-reset") setNotice(n);
    });
  }, [hosted]);

  if (!hosted || !notice) return null;
  const from = LABEL[notice.from] ? t(LABEL[notice.from]!) : notice.from;
  const to = LABEL[notice.to] ? t(LABEL[notice.to]!) : notice.to;

  return (
    <div
      role="status"
      data-testid="mode-reset-banner"
      className="mx-3 mt-1 flex shrink-0 items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-text-secondary md:mx-4"
    >
      <span className="min-w-0 flex-1">{t("perm.mode.reset.notice", { from, to })}</span>
      <button
        type="button"
        onClick={() => setNotice(null)}
        aria-label={t("perm.mode.reset.dismiss")}
        className="focus-ring rounded-md p-1 text-text-muted transition-colors hover:bg-bg-surface hover:text-text-primary"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
