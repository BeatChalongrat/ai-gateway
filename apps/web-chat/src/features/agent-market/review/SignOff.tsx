"use client";

// AM3 — sign-off. A reviewer (the run's owner, or a colleague it was shared
// with "may review") marks a finished run reviewed: their name, the time and
// the note go on the face of a new version of each file (the report's first
// lines, the deck's first slide, the workbook's first sheet) and into the
// run's audit trail. The unsigned files stay in the history.

import { useState } from "react";
import { BadgeCheck } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

import { amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { formatDateTime } from "../format";
import type { Review } from "../types";
import { ErrorPanel, Pill, Thai, type Tone } from "../ui";

export function SignOffButton({
  runId,
  shareId,
  onDone,
  small = true,
}: {
  runId?: string;
  shareId?: string;
  onDone?: (r: Review) => void;
  small?: boolean;
}) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Review | null>(null);

  const submit = () => {
    const body = { name: name.trim() || undefined, note: note.trim() || undefined };
    const p = shareId ? amApi.sharedReview(shareId, body) : runId ? amApi.review(runId, body) : null;
    if (!p) return;
    setBusy(true);
    setError(null);
    p.then(
      (r) => {
        setBusy(false);
        setDone(r.review);
        onDone?.(r.review);
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };
  const close = () => {
    setOpen(false);
    if (done) {
      setDone(null);
      setNote("");
    }
  };

  return (
    <>
      <Button type="button" variant="secondary" size={small ? "sm" : "md"} onClick={() => setOpen(true)} data-testid="am-signoff-open">
        <BadgeCheck width={14} height={14} aria-hidden />
        <span>{t("review.button")}</span>
      </Button>
      <Thai>
        <Modal open={open} onClose={close} title={t("review.title")} description={t("review.body")} testId="am-signoff-dialog">
          {done ? (
            <div className="flex flex-col gap-3">
              <p role="status" className="rounded-lg border border-status-success/40 bg-status-success/10 px-3 py-2 text-sm text-text-primary" data-testid="am-signoff-done">
                {t("review.done", { name: done.reviewer_name })}
              </p>
              <Button type="button" variant="secondary" size="sm" onClick={close}>
                {t("common.close")}
              </Button>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy) submit();
              }}
              className="flex flex-col gap-3"
            >
              <label className="block text-sm">
                <span className="text-text-secondary">{t("review.name")}</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={120}
                  placeholder={t("review.namePlaceholder")}
                  className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
                  data-testid="am-signoff-name"
                />
                <span className="mt-1 block text-[11px] text-text-muted">{t("review.nameHint")}</span>
              </label>
              <label className="block text-sm">
                <span className="text-text-secondary">{t("review.note")}</span>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={2}
                  maxLength={500}
                  placeholder={t("review.notePlaceholder")}
                  className="focus-ring mt-1 w-full resize-y rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
                  data-testid="am-signoff-note"
                />
              </label>
              <p className="text-[11px] leading-relaxed text-text-muted">{t("review.what")}</p>
              {error ? <ErrorPanel message={error} /> : null}
              <div className="flex gap-2">
                <Button type="submit" size="sm" loading={busy} data-testid="am-signoff-send">
                  <span>{t("review.send")}</span>
                </Button>
                <Button type="button" variant="secondary" size="sm" onClick={close} disabled={busy}>
                  {t("common.cancel")}
                </Button>
              </div>
            </form>
          )}
        </Modal>
      </Thai>
    </>
  );
}

const STAMP: Record<string, { key: AmKey; tone: Tone }> = {
  queued: { key: "review.stamp.queued", tone: "neutral" },
  running: { key: "review.stamp.queued", tone: "neutral" },
  succeeded: { key: "review.stamp.done", tone: "success" },
  failed: { key: "review.stamp.failed", tone: "warning" },
};

export function ReviewsList({ reviews }: { reviews: Review[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  if (!reviews.length) return <p className="text-sm text-text-muted">{t("review.none")}</p>;
  return (
    <ul className="flex flex-col gap-2" data-testid="am-reviews">
      {reviews.map((r) => {
        const st = STAMP[r.stamp_status ?? ""];
        return (
          <li key={r.id} className="rounded-lg border border-border-subtle bg-bg-base px-3 py-2" data-testid="am-review">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <BadgeCheck width={15} height={15} className="text-status-success" aria-hidden />
              <span className="font-medium text-text-primary">{r.reviewer_name}</span>
              <span className="text-xs text-text-muted">{`${r.reviewer_email} · ${formatDateTime(r.created_at, locale)}`}</span>
              {r.via_share_id ? <Pill>{t("review.viaShare")}</Pill> : null}
              {st ? <Pill tone={st.tone}>{t(st.key)}</Pill> : null}
            </div>
            {r.note ? <p className="mt-1 text-xs text-text-secondary">{t("review.noteLine", { note: r.note })}</p> : null}
          </li>
        );
      })}
    </ul>
  );
}
