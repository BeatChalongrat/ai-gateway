"use client";

// AM6 — a drafted e-mail about a finished round.
//
// Drafting needs no connector and no permission: the draft is a card in the
// thread with its subject, its body and the round's own files, and "คัดลอก"
// puts the subject and the body on the clipboard — most of the value is
// there. The body is text a person will paste: it is shown as text, with its
// line breaks, never as HTML or Markdown, and nothing in it becomes a link.
//
// Sending needs a configured mail connector and the person approving that
// very message. The recipients are typed by the person — the fields start
// empty whatever the draft says — and nothing is sent until the dialog that
// shows exactly what goes (to, cc, subject, body, the files) is confirmed.
// The server checks the approval against the draft's hash. Never automatic,
// never in bulk.

import { useState } from "react";
import { Check, Copy, Mail } from "lucide-react";

import { AttachmentCard } from "@/components/chat/AttachmentCard";
import { useCopyText } from "@/components/chat/MessageParts";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT } from "../i18n";
import { formatDateTime } from "../format";
import type { EmailDraft, EmailSend } from "../types";
import { cls } from "../ui";

const RECIPIENT_CODES = new Set(["NO_RECIPIENT", "BAD_RECIPIENT"]);

/** "a@x.example, b@x.example" → the addresses the person typed, in their order. */
export function recipients(text: string): string[] {
  return text
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** "ร่างอีเมล" on a finished round's files card: the draft arrives in the thread. */
export function DraftEmailButton({ runId }: { runId: string }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const ask = () => {
    setBusy(true);
    setNote(null);
    amApi.draftEmail(runId).then(
      () => {
        setBusy(false);
        setNote({ text: t("email.drafted"), error: false });
      },
      (err) => {
        setBusy(false);
        setNote({ text: errorMessage(err), error: true });
      },
    );
  };
  return (
    <>
      <Button type="button" variant="secondary" size="sm" onClick={ask} loading={busy}>
        {busy ? null : <Mail width={13} height={13} aria-hidden />}
        <span>{t("email.draft")}</span>
      </Button>
      {note ? (
        // order-last + w-full: its own line under the card's button row.
        <p
          role={note.error ? "alert" : "status"}
          className={cls("order-last w-full text-[11px]", note.error ? "text-status-danger" : "text-text-secondary")}
          data-testid={note.error ? undefined : "am-email-drafted"}
        >
          {note.text}
        </p>
      ) : null}
    </>
  );
}

export function EmailDraftCard({ draft }: { draft: EmailDraft }) {
  const t = useAmT();
  const subject = draft.subject ?? "";
  const body = draft.body ?? "";
  const { copied, copy } = useCopyText(`${subject}\n\n${body}`);
  // what this page sent, until the thread's copy of the draft lists it
  const [mine, setMine] = useState<EmailSend[]>([]);
  const listed = draft.sent ?? [];
  const sent = [...listed, ...mine.filter((s) => !listed.some((x) => x.id === s.id))];
  const files = draft.attachments ?? [];

  return (
    <section className="rounded-xl border border-border bg-bg-surface px-4 py-3" aria-label={t("email.title")} data-testid="am-email-draft">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
        <Mail width={15} height={15} className="shrink-0 text-text-secondary" aria-hidden />
        <span>{t("email.title")}</span>
      </h3>
      <p className="mt-2 text-xs text-text-muted">{t("email.subject")}</p>
      <p className="break-words text-sm font-medium text-text-primary" data-testid="am-email-subject">
        {subject}
      </p>
      <p className="mt-2 text-xs text-text-muted">{t("email.body")}</p>
      {/* text, as it will be pasted: never Markdown, never a link */}
      <p className="mt-0.5 whitespace-pre-wrap break-words rounded-lg bg-bg-base px-3 py-2 text-sm text-text-primary" data-testid="am-email-body">
        {body}
      </p>
      {files.length ? (
        <div className="mt-2">
          <p className="text-xs text-text-muted">{t("email.attachments")}</p>
          <div className="mt-1 flex flex-col gap-1.5">
            {files.map((f) => (
              <EmailFile key={f.artifact_id} id={f.artifact_id} name={f.name} size={f.size} />
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" size="sm" onClick={() => void copy()}>
          {copied ? <Check width={13} height={13} className="text-status-success" aria-hidden /> : <Copy width={13} height={13} aria-hidden />}
          <span>{copied ? t("email.copied") : t("email.copy")}</span>
        </Button>
      </div>
      {draft.can_send ? (
        <SendForm draft={draft} onSent={(s) => setMine((cur) => [...cur, s])} />
      ) : draft.cannot_send_reason ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="am-email-cannot-send">
          {draft.cannot_send_reason}
        </p>
      ) : null}
      {sent.length ? <SentList sent={sent} /> : null}
    </section>
  );
}

function EmailFile({ id, name, size }: { id: string; name: string; size: number }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const download = () => {
    setBusy(true);
    amApi.downloadArtifact(id, name).then(
      () => setBusy(false),
      () => setBusy(false),
    );
  };
  return <AttachmentCard filename={name} sizeBytes={size} onDownload={download} busy={busy} downloadLabel={t("common.downloadNamed", { name })} />;
}

function SendForm({ draft, onSent }: { draft: EmailDraft; onSent: (s: EmailSend) => void }) {
  const t = useAmT();
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [toError, setToError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const tos = recipients(to);
  const ccs = recipients(cc);
  const field = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

  /** The dialog, never the send: the person reads what goes first. */
  const review = () => {
    setError(null);
    if (!tos.length) {
      setToError(t("email.noRecipient"));
      return;
    }
    setToError(null);
    setConfirming(true);
  };

  const sendNow = () => {
    setBusy(true);
    amApi.sendEmail(draft.id, { to: tos, cc: ccs, body_sha256: draft.body_sha256, approve: true }).then(
      (r) => {
        setBusy(false);
        setConfirming(false);
        setTo("");
        setCc("");
        if (r?.send) onSent(r.send);
        // AM7: a send the relay refused says why (the draft is still there to copy)
        if (r?.message) setError(r.message);
      },
      (err) => {
        setBusy(false);
        setConfirming(false);
        if (err instanceof AmError && RECIPIENT_CODES.has(err.code)) setToError(errorMessage(err));
        else setError(errorMessage(err));
      },
    );
  };

  const names = (draft.attachments ?? []).map((f) => f.name);
  return (
    <>
      <form
        className="mt-3 flex flex-col gap-2 border-t border-border-subtle pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) review();
        }}
      >
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <label htmlFor={`am-email-to-${draft.id}`} className="text-xs text-text-secondary">
              {t("email.to")}
            </label>
            <input
              id={`am-email-to-${draft.id}`}
              type="text"
              inputMode="email"
              autoComplete="off"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-invalid={toError ? true : undefined}
              aria-describedby={`am-email-hint-${draft.id}`}
              className={field}
            />
            {toError ? (
              <p role="alert" className="mt-1 text-xs text-status-danger" data-testid="am-email-to-error">
                {toError}
              </p>
            ) : null}
          </div>
          <div>
            <label htmlFor={`am-email-cc-${draft.id}`} className="text-xs text-text-secondary">
              {t("email.cc")}
            </label>
            <input
              id={`am-email-cc-${draft.id}`}
              type="text"
              inputMode="email"
              autoComplete="off"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              aria-describedby={`am-email-hint-${draft.id}`}
              className={field}
            />
          </div>
        </div>
        <p id={`am-email-hint-${draft.id}`} className="text-[11px] text-text-muted">
          {t("email.recipientsHint")}
        </p>
        <div>
          <Button type="submit" size="sm" disabled={busy}>
            <Mail width={13} height={13} aria-hidden />
            <span>{t("email.send")}</span>
          </Button>
        </div>
        {error ? (
          <p role="alert" className="text-xs text-status-danger" data-testid="am-email-error">
            {error}
          </p>
        ) : null}
      </form>
      <Modal
        open={confirming}
        onClose={() => !busy && setConfirming(false)}
        title={t("email.confirmTitle")}
        description={t("email.confirmBody")}
        maxWidth={560}
        testId="am-email-confirm"
        footer={
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setConfirming(false)} disabled={busy}>
              {t("common.cancel")}
            </Button>
            <Button type="button" size="sm" onClick={sendNow} loading={busy}>
              <span>{busy ? t("email.sending") : t("email.confirm")}</span>
            </Button>
          </div>
        }
      >
        <dl className="flex flex-col gap-2.5 text-sm">
          <div>
            <dt className="text-xs text-text-muted">{t("email.to")}</dt>
            <dd className="break-words text-text-primary" data-testid="am-email-confirm-to">
              {tos.join(", ")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">{t("email.cc")}</dt>
            <dd className="break-words text-text-primary" data-testid="am-email-confirm-cc">
              {ccs.length ? ccs.join(", ") : t("email.none")}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">{t("email.subject")}</dt>
            <dd className="break-words text-text-primary" data-testid="am-email-confirm-subject">
              {draft.subject}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">{t("email.body")}</dt>
            <dd className="scrollbar-thin max-h-60 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-bg-base px-3 py-2 text-text-primary" data-testid="am-email-confirm-body">
              {draft.body}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">{t("email.attachments")}</dt>
            <dd className="break-words text-text-primary" data-testid="am-email-confirm-files">
              {names.length ? names.join(", ") : t("email.none")}
            </dd>
          </div>
        </dl>
      </Modal>
    </>
  );
}

function SentList({ sent }: { sent: EmailSend[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  return (
    <div className="mt-3 border-t border-border-subtle pt-2" data-testid="am-email-sent">
      <p className="text-xs font-medium text-text-primary">{t("email.sentTitle")}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {sent.map((s) => {
          const vars = {
            to: (s.to ?? []).join(", "),
            when: formatDateTime(s.at, locale),
            who: s.approved_by?.name || s.approved_by?.email || "",
          };
          return (
            <li key={s.id} className={cls("text-xs", s.status === "failed" ? "text-status-danger" : "text-text-secondary")}>
              <span>{s.status === "failed" ? t("email.failedLine", vars) : t("email.sentLine", vars)}</span>
              {s.cc?.length ? <span className="block text-text-muted">{t("email.sentCc", { cc: s.cc.join(", ") })}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
