"use client";

// AM16 — the one sentence a run puts on the face of its card when something of
// it was sent to a model outside the organisation as it was typed: text that
// was not masked, or the image of a page (an image is never masked). On the
// run's own progress card as soon as it has happened — whatever the run then
// comes to — and on the card of its files. Not in the folded details: the
// owner's condition for sending documents out unmasked is that the run says so
// where nobody has to look for it.

import { TriangleAlert } from "lucide-react";

import { useAmT } from "../i18n";

export function SentOutUnmasked() {
  const t = useAmT();
  return (
    <div
      role="note"
      className="mt-2 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2"
      data-testid="am-sent-out-unmasked"
    >
      <p className="flex items-start gap-1.5 text-sm font-semibold text-text-primary">
        <TriangleAlert width={15} height={15} className="mt-0.5 shrink-0 text-status-warning" aria-hidden />
        <span>{t("sentOut.title")}</span>
      </p>
      <p className="mt-0.5 text-xs text-text-secondary">{t("sentOut.detail")}</p>
    </div>
  );
}
