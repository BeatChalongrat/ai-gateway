"use client";

// AM6 — "เตือนให้ทำรอบ": nothing runs by itself. A reminder asks a person to
// feed the agent: when it falls due the server opens a round that waits for
// files and tells the person; the round starts when they send the files.
// Each reminder can be snoozed (1, 3 or 7 days), skipped for this round,
// stopped and started again, or deleted. Under the list: the server's own
// sentence that nothing runs without a person, and what a reminder reaches.
//
// The browser's permission to notify is asked for at one moment only: when
// a person adds their first reminder (a click, so the browser shows its
// prompt). Never on page load, and never in the desktop app, whose host
// shows its own notifications.

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useHostedMode } from "@/hooks/useHostedMode";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import type { Reminder, ReminderReaches, ReminderRule } from "../types";
import { ErrorPanel, Pill } from "../ui";
import { workspaceHref } from "../workspace/context";

const SNOOZE_DAYS = [1, 3, 7] as const;
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const FIELD = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted";

/** The round a reminder opened, in the workspace. */
export const roundHref = (installId: string, runId: string) => `${workspaceHref(installId)}?run=${encodeURIComponent(runId)}`;

/** What a reminder reaches, as one line. */
export function reachesLine(r: ReminderReaches | null | undefined, t: (k: AmKey) => string): string {
  if (!r) return "";
  const parts: string[] = [];
  if (r.in_app) parts.push(t("remind.reach.inApp"));
  if (r.desktop) parts.push(t("remind.reach.desktop"));
  if (r.browser) parts.push(t("remind.reach.browser"));
  parts.push(r.email ? t("remind.reach.email") : t("remind.reach.noEmail"));
  return parts.join(" · ");
}

/** Ask the browser once, while the click that adds the first reminder is still the person's. */
function askToNotify(): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "default") return;
    const asked = Notification.requestPermission() as Promise<NotificationPermission> | undefined;
    void asked?.catch?.(() => {});
  } catch {
    /* a browser without the prompt: in-app notifications still reach the person */
  }
}

export function RemindersSection({ installId }: { installId: string }) {
  const t = useAmT();
  const [list, setList] = useState<Reminder[] | null>(null);
  const [reaches, setReaches] = useState<ReminderReaches | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    let alive = true;
    setError(null);
    amApi.reminders(installId).then(
      (r) => {
        if (!alive) return;
        setList(r?.reminders ?? []);
        setReaches(r?.reaches ?? null);
        setNote(r?.note ?? "");
      },
      (err) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  useEffect(load, [load]);

  const replace = (r: Reminder) => setList((cur) => (cur ?? []).map((x) => (x.id === r.id ? r : x)));
  const remove = (id: string) => setList((cur) => (cur ?? []).filter((x) => x.id !== id));
  const reach = reachesLine(reaches, t);

  return (
    <section className="mt-8" aria-labelledby="am-reminders-title" data-testid="am-reminders">
      <h2 id="am-reminders-title" className="text-sm font-semibold text-text-primary">
        {t("remind.title")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{t("remind.body")}</p>
      {error && !list ? (
        <div className="mt-2.5">
          <ErrorPanel message={errorMessage(error)} onRetry={load} />
        </div>
      ) : null}
      {list ? (
        <div className="mt-2.5 flex flex-col gap-2">
          {list.length ? (
            list.map((r) => <ReminderRow key={r.id} installId={installId} reminder={r} onChange={replace} onRemoved={remove} />)
          ) : (
            <p className="rounded-xl border border-border-subtle px-4 py-3 text-sm text-text-muted">{t("remind.none")}</p>
          )}
          <AddReminder installId={installId} first={list.length === 0} onAdded={(r) => setList((cur) => [...(cur ?? []), r])} />
        </div>
      ) : null}
      {note ? (
        <p className="mt-2 text-xs text-text-secondary" data-testid="am-reminders-note">
          {note}
        </p>
      ) : null}
      {reach ? (
        <p className="mt-0.5 text-[11px] text-text-muted" data-testid="am-reminders-reaches">
          {reach}
        </p>
      ) : null}
    </section>
  );
}

function ReminderRow({
  installId,
  reminder: r,
  onChange,
  onRemoved,
}: {
  installId: string;
  reminder: Reminder;
  onChange: (r: Reminder) => void;
  onRemoved: (id: string) => void;
}) {
  const t = useAmT();
  const [snoozing, setSnoozing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stopped = r.status === "stopped";

  const act = (what: string, call: () => Promise<{ reminder: Reminder } | null>) => {
    setBusy(what);
    setError(null);
    call().then(
      (res) => {
        setBusy(null);
        setSnoozing(false);
        if (what === "delete") onRemoved(r.id);
        else if (res?.reminder) onChange(res.reminder);
      },
      (err) => {
        setBusy(null);
        setError(errorMessage(err));
      },
    );
  };

  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-reminder" data-status={r.status}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-sm font-medium text-text-primary">{r.label}</span>
        <Pill tone={stopped ? "neutral" : "success"}>{stopped ? t("remind.status.stopped") : t("remind.status.active")}</Pill>
      </div>
      <p className="mt-0.5 text-sm text-text-secondary">{[r.rule_text, stopped ? "" : r.next_due_text].filter(Boolean).join(" · ")}</p>
      {r.waiting_run_id ? (
        <p className="mt-1 text-xs text-text-primary" data-testid="am-reminder-waiting">
          <span>{r.period_label ? t("remind.waiting", { period: r.period_label }) : t("remind.waitingNoPeriod")}</span>
          <span aria-hidden>{" · "}</span>
          <Link href={roundHref(installId, r.waiting_run_id)} className="focus-ring rounded underline underline-offset-2 hover:text-text-primary">
            {t("remind.openRound")}
          </Link>
        </p>
      ) : null}
      <div className="mt-2.5 flex flex-wrap items-center gap-2" role="group" aria-label={t("remind.actions", { label: r.label })}>
        {!stopped ? (
          snoozing ? (
            SNOOZE_DAYS.map((d) => (
              <Button key={d} type="button" variant="secondary" size="sm" onClick={() => act("snooze", () => amApi.snoozeReminder(r.id, d))} disabled={!!busy}>
                {t("remind.snoozeDays", { n: String(d) })}
              </Button>
            ))
          ) : (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={() => setSnoozing(true)} disabled={!!busy}>
                {t("remind.snooze")}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => act("skip", () => amApi.skipReminder(r.id))} loading={busy === "skip"}>
                <span>{t("remind.skip")}</span>
              </Button>
            </>
          )
        ) : null}
        {stopped ? (
          <Button type="button" variant="secondary" size="sm" onClick={() => act("resume", () => amApi.resumeReminder(r.id))} loading={busy === "resume"}>
            <span>{t("remind.resume")}</span>
          </Button>
        ) : (
          <Button type="button" variant="secondary" size="sm" onClick={() => act("stop", () => amApi.stopReminder(r.id))} loading={busy === "stop"}>
            <span>{t("remind.stop")}</span>
          </Button>
        )}
        <Button type="button" variant="ghost" size="sm" onClick={() => act("delete", () => amApi.deleteReminder(r.id))} loading={busy === "delete"}>
          <span>{t("remind.delete")}</span>
        </Button>
      </div>
      {error ? (
        <p role="alert" className="mt-1.5 text-xs text-status-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function AddReminder({ installId, first, onAdded }: { installId: string; first: boolean; onAdded: (r: Reminder) => void }) {
  const t = useAmT();
  const hosted = useHostedMode();
  const id = useId();
  const [label, setLabel] = useState("");
  const [every, setEvery] = useState<"month" | "week">("month");
  const [day, setDay] = useState(1);
  const [weekday, setWeekday] = useState(1);
  const [time, setTime] = useState("09:00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const name = label.trim();
    if (!name) {
      setError(t("remind.needLabel"));
      return;
    }
    if (!TIME.test(time)) {
      setError(t("remind.badTime"));
      return;
    }
    if (first && !hosted) askToNotify();
    const rule: ReminderRule = every === "month" ? { every, day, time } : { every, weekday, time };
    setBusy(true);
    setError(null);
    amApi.addReminder(installId, { label: name, rule }).then(
      (r) => {
        setBusy(false);
        setLabel("");
        if (r?.reminder) onAdded(r.reminder);
      },
      (err) => {
        setBusy(false);
        setError(errorMessage(err));
      },
    );
  };

  return (
    <form
      className="rounded-xl border border-dashed border-border px-4 py-3"
      data-testid="am-reminder-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) submit();
      }}
    >
      <h3 className="text-sm font-medium text-text-primary">{t("remind.addTitle")}</h3>
      <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
        <div>
          <label htmlFor={`${id}-label`} className="text-xs text-text-secondary">
            {t("remind.label")}
          </label>
          <input id={`${id}-label`} value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder={t("remind.labelPlaceholder")} className={FIELD} />
        </div>
        <div>
          <label htmlFor={`${id}-every`} className="text-xs text-text-secondary">
            {t("remind.every")}
          </label>
          <select id={`${id}-every`} value={every} onChange={(e) => setEvery(e.target.value === "week" ? "week" : "month")} className={FIELD}>
            <option value="month">{t("remind.every.month")}</option>
            <option value="week">{t("remind.every.week")}</option>
          </select>
        </div>
        {every === "month" ? (
          <div>
            <label htmlFor={`${id}-day`} className="text-xs text-text-secondary">
              {t("remind.day")}
            </label>
            <select id={`${id}-day`} value={day} onChange={(e) => setDay(Number(e.target.value))} className={FIELD}>
              {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div>
            <label htmlFor={`${id}-weekday`} className="text-xs text-text-secondary">
              {t("remind.weekday")}
            </label>
            <select id={`${id}-weekday`} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} className={FIELD}>
              {WEEKDAYS.map((d) => (
                <option key={d} value={d}>
                  {t(`remind.weekday.${d}` as AmKey)}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label htmlFor={`${id}-time`} className="text-xs text-text-secondary">
            {t("remind.time")}
          </label>
          <input id={`${id}-time`} type="time" value={time} onChange={(e) => setTime(e.target.value)} className={FIELD} />
        </div>
      </div>
      {error ? (
        <p role="alert" className="mt-1.5 text-xs text-status-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-2.5">
        <Button type="submit" size="sm" loading={busy}>
          <span>{t("remind.add")}</span>
        </Button>
      </div>
    </form>
  );
}
