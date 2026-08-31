"use client";

// FIX34 Part A — /quick: the quick-ask window's page (desktop only). A Next
// page may export nothing but the page itself, so the component lives in
// components/quick/QuickAsk.tsx (the doc comment is there too).

import { QuickAsk } from "@/components/quick/QuickAsk";
import { useHostedMode } from "@/hooks/useHostedMode";
import { useT } from "@/lib/i18n";

export default function QuickAskPage() {
  const hosted = useHostedMode();
  const t = useT();
  if (!hosted) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center">
        <p className="max-w-sm text-sm text-text-muted">{t("desktopOnly.notice")}</p>
      </div>
    );
  }
  return <QuickAsk />;
}
