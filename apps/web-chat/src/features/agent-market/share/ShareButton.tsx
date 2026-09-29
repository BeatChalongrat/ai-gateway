"use client";

// AM3 — "แชร์ให้เพื่อนร่วมงาน". The owner names a colleague in the
// organisation (their email, with suggestions); the colleague opens the link
// signed in as that person — nothing is public, nothing leaves the
// organisation, and nothing is emailed for you (copy the link and send it
// yourself). Read only, or allowed to sign off. Every share can be revoked.

import { useEffect, useState } from "react";
import { Copy, Share2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

import { amApi, errorMessage } from "../api";
import { useAmLocale, useAmT } from "../i18n";
import { formatDate } from "../format";
import type { Person, Share } from "../types";
import { ErrorPanel, Pill, Thai } from "../ui";

export const EXPIRY_DAYS = [7, 30, 90, 0] as const;

export function shareLink(path: string): string {
  try {
    return typeof window !== "undefined" ? `${window.location.origin}${path}` : path;
  } catch {
    return path;
  }
}

export function ShareButton({ runId }: { runId: string }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [canReview, setCanReview] = useState(false);
  const [days, setDays] = useState<number>(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [shares, setShares] = useState<Share[]>([]);
  const [people, setPeople] = useState<Person[]>([]);

  const loadShares = () =>
    amApi.shares(runId).then(
      (r) => setShares((r?.shares ?? []).filter((s) => !s.revoked_at)),
      () => {},
    );

  useEffect(() => {
    if (open) void loadShares();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, runId]);

  // suggestions from the access list and the organisation's signed-up people
  useEffect(() => {
    if (!open) return;
    const q = email.trim();
    if (q.length < 2 || q.includes(" ")) {
      setPeople([]);
      return;
    }
    let alive = true;
    const id = setTimeout(() => {
      amApi.people(q).then(
        (r) => alive && setPeople(r?.people ?? []),
        () => alive && setPeople([]),
      );
    }, 250);
    return () => {
      alive = false;
      clearTimeout(id);
    };
  }, [email, open]);

  const submit = () => {
    setBusy(true);
    setError(null);
    setLink(null);
    amApi.share(runId, { email: email.trim(), can_review: canReview, expires_days: days || undefined }).then(
      (r) => {
        setBusy(false);
        setLink(shareLink(r.path));
        setEmail("");
        void loadShares();
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };

  const copy = (text: string) => {
    try {
      void navigator.clipboard?.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* the link stays on screen to copy by hand */
    }
  };

  const revoke = (s: Share) => {
    amApi.revokeShare(s.id).then(
      () => setShares((cur) => cur.filter((x) => x.id !== s.id)),
      (err) => setError(errorMessage(err)),
    );
  };

  return (
    <>
      <Button type="button" variant="secondary" size="sm" onClick={() => setOpen(true)} data-testid="am-share-open">
        <Share2 width={14} height={14} aria-hidden />
        <span>{t("share.button")}</span>
      </Button>
      <Thai>
        <Modal open={open} onClose={() => setOpen(false)} title={t("share.title")} description={t("share.body")} testId="am-share-dialog">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy && email.trim()) submit();
            }}
            className="flex flex-col gap-3"
          >
            <label className="block text-sm">
              <span className="text-text-secondary">{t("share.email")}</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="off"
                placeholder={t("share.emailPlaceholder")}
                className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
                data-testid="am-share-email"
              />
            </label>
            {people.length ? (
              <ul className="flex flex-wrap gap-1.5" data-testid="am-share-people">
                {people.map((p) => (
                  <li key={p.email}>
                    <Button type="button" variant="secondary" size="sm" onClick={() => setEmail(p.email)}>
                      {p.name ? `${p.name} · ${p.email}` : p.email}
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
            <label className="flex items-start gap-2 text-sm text-text-primary">
              <input type="checkbox" checked={canReview} onChange={(e) => setCanReview(e.target.checked)} className="mt-0.5 h-4 w-4 accent-current" data-testid="am-share-review" />
              <span>
                {t("share.canReview")}
                <span className="mt-0.5 block text-xs text-text-muted">{t("share.canReviewHint")}</span>
              </span>
            </label>
            <label className="block text-sm">
              <span className="text-text-secondary">{t("share.expiry")}</span>
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="focus-ring mt-1 w-full rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary"
                data-testid="am-share-expiry"
              >
                {EXPIRY_DAYS.map((d) => (
                  <option key={d} value={d}>
                    {d ? t("share.days", { n: String(d) }) : t("share.never")}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-[11px] leading-relaxed text-text-muted">{t("share.rules")}</p>
            {error ? <ErrorPanel message={error} /> : null}
            <div className="flex gap-2">
              <Button type="submit" size="sm" loading={busy} disabled={!email.trim()} data-testid="am-share-send">
                <span>{t("share.send")}</span>
              </Button>
            </div>
          </form>
          {link ? (
            <div className="mt-4 rounded-lg border border-status-success/40 bg-status-success/10 px-3 py-2" data-testid="am-share-link">
              <p className="text-xs text-text-primary">{t("share.linkReady")}</p>
              <div className="mt-1.5 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-bg-surface px-2 py-1 text-[11px] text-text-primary">{link}</code>
                <Button type="button" variant="secondary" size="sm" onClick={() => copy(link)}>
                  <Copy width={13} height={13} aria-hidden />
                  <span>{copied ? t("share.copied") : t("share.copy")}</span>
                </Button>
              </div>
            </div>
          ) : null}
          {shares.length ? (
            <div className="mt-5">
              <h3 className="text-xs font-medium text-text-primary">{t("share.current")}</h3>
              <ul className="mt-1.5 flex flex-col gap-1.5" data-testid="am-share-list">
                {shares.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-border-subtle px-2.5 py-1.5 text-xs">
                    <span className="min-w-0 flex-1 truncate text-text-primary">{s.recipient_email}</span>
                    {s.can_review ? <Pill>{t("share.canReviewTag")}</Pill> : null}
                    <span className="text-text-muted">
                      {s.expires_at ? t("share.until", { when: formatDate(s.expires_at, locale) }) : t("share.never")}
                      {s.last_opened_at ? ` · ${t("share.opened")}` : ` · ${t("share.notOpened")}`}
                    </span>
                    <Button type="button" variant="secondary" size="sm" onClick={() => copy(shareLink(`/chat/agents/shared/${s.id}`))} aria-label={t("share.copyFor", { email: s.recipient_email })}>
                      <Copy width={12} height={12} aria-hidden />
                    </Button>
                    <Button type="button" variant="secondary" size="sm" onClick={() => revoke(s)} aria-label={t("share.revokeFor", { email: s.recipient_email })} data-testid="am-share-revoke">
                      <Trash2 width={12} height={12} aria-hidden />
                      <span>{t("share.revoke")}</span>
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Modal>
      </Thai>
    </>
  );
}
