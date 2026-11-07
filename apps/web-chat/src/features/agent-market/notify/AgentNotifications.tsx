"use client";

// AM6 — what an agent has to tell a person, in the app. Mounted once, in the
// chat's layout, and only when Agent Market is available to this person.
//
// Nothing runs by itself: a reminder that falls due opens a round that waits
// for files, and a round parked on a question waits for its answer. Both
// arrive here as notifications (GET /notifications?surface=web), read on the
// rail's rhythm — every 30 s while the page is visible, and when the window
// regains focus. The newest is shown in the chat's banner strip and stays
// until it is acted on or closed.
//
// AM9 — a notification reaches every surface the person has: this page lists
// what nobody has acted on yet (the server no longer leaves out what another
// client showed), so a browser sees what the desktop app's page showed, and
// the other way round. Acting on it here dismisses it everywhere, and a page
// drops what was acted on elsewhere at its next read. The browser's own
// notification is raised once per browser (remembered in its storage).
//
// Where the browser already allows notifications, the same one is raised
// there too. The permission is never asked for here (only when a person adds
// their first reminder), and nothing is raised in the desktop app's page: its
// host shows its own.

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { BannerStrip } from "@/components/chat/UpdateBanner";
import { Button } from "@/components/ui/Button";
import { toast, toastError } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";

import { amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import type { AmNotification, Reminder } from "../types";
import { Thai } from "../ui";
import { useAgentMarket } from "../useAgentMarket";
import { RAIL_POLL_MS } from "../useRail";

const REMINDER_KINDS = new Set(["reminder_due", "reminder_followup"]);

/** The notifications this browser has raised already (AM9: once per browser, however many tabs). */
const RAISED_KEY = "am.raisedNotifications";
const RAISED_KEEP = 200;

function raisedHere(): string[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(RAISED_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** True the first time this browser asks for this id. */
export function firstRaiseHere(id: string): boolean {
  const seen = raisedHere();
  if (seen.includes(id)) return false;
  try {
    window.localStorage.setItem(RAISED_KEY, JSON.stringify([...seen, id].slice(-RAISED_KEEP)));
  } catch {
    /* storage unavailable: the banner still says it */
  }
  return true;
}

/** A path of this app ("/chat/…"), never another site or a protocol-relative one. */
export function isAppPath(link: string | null | undefined): link is string {
  return !!link && /^\/(?![/\\])/.test(link);
}

export function AgentNotifications() {
  const { state } = useAgentMarket();
  if (state !== "available") return null;
  return (
    <Thai>
      <Host />
    </Thai>
  );
}

function byNewest(a: AmNotification, b: AmNotification): number {
  return (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
}

/** The browser may show its own notification (the person already allowed it). */
function canRaise(): boolean {
  try {
    return typeof Notification !== "undefined" && Notification.permission === "granted";
  } catch {
    return false;
  }
}

/** The browser's own notification, where the person already allowed it. */
function raise(n: AmNotification, onClick: () => void): void {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const note = new Notification(n.title, { body: n.body, tag: `am-${n.id}` });
    note.onclick = () => {
      try {
        window.focus();
      } catch {
        /* a window that cannot be focused still navigates */
      }
      onClick();
      note.close();
    };
  } catch {
    /* no notification in this browser: the banner says it */
  }
}

function Host() {
  const t = useAmT();
  const router = useRouter();
  const hosted = useHostedMode();
  const [queue, setQueue] = useState<AmNotification[]>([]);
  const [busy, setBusy] = useState(false);
  const handled = useRef(new Set<string>());
  const shown = useRef(new Set<string>());

  const load = useCallback(() => {
    amApi.notifications("web").then(
      (r) => {
        // the server's list is what is still waiting: one acted on in another client drops out here too
        const got = (r?.notifications ?? []).filter((n) => !!n?.id && !handled.current.has(n.id));
        setQueue(got.sort(byNewest));
      },
      () => {
        /* the next read tries again */
      },
    );
  }, []);

  // the rail's rhythm and visibility rules (../useRail)
  useEffect(() => {
    load();
    const tick = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") load();
    }, RAIL_POLL_MS);
    const onFocus = () => load();
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(tick);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  /** Acted on or closed: gone from this page, and from every surface. */
  const done = useCallback((n: AmNotification) => {
    handled.current.add(n.id);
    setQueue((cur) => cur.filter((x) => x.id !== n.id));
    amApi.dismissNotification(n.id).then(
      () => {},
      () => {},
    );
  }, []);

  const open = useCallback(
    (n: AmNotification) => {
      if (isAppPath(n.link)) router.push(n.link);
      done(n);
    },
    [router, done],
  );

  const current = queue[0] ?? null;
  useEffect(() => {
    if (!current || shown.current.has(current.id)) return;
    shown.current.add(current.id);
    amApi.notificationShown(current.id, "web").then(
      () => {},
      () => {},
    );
    if (!hosted && canRaise() && firstRaiseHere(current.id)) raise(current, () => open(current));
  }, [current, hosted, open]);

  if (!current) return null;
  const n = current;
  const forReminder = REMINDER_KINDS.has(n.kind) && !!n.reminder_id;

  const onReminder = (call: (id: string) => Promise<{ reminder: Reminder }>, said: AmKey) => {
    if (!n.reminder_id) return;
    setBusy(true);
    call(n.reminder_id).then(
      () => {
        setBusy(false);
        toast(t(said));
        done(n);
      },
      (err) => {
        setBusy(false);
        toastError(errorMessage(err));
      },
    );
  };

  return (
    <BannerStrip
      data-testid="am-notification"
      data-kind={n.kind}
      aria-label={t("notify.aria")}
      wrap
      onDismiss={() => done(n)}
      dismissLabel={t("notify.close")}
      actions={
        <div className="flex flex-wrap items-center gap-1.5">
          {isAppPath(n.link) ? (
            <Button type="button" size="sm" onClick={() => open(n)} disabled={busy}>
              {t("notify.open")}
            </Button>
          ) : null}
          {forReminder ? (
            <>
              <Button type="button" variant="secondary" size="sm" onClick={() => onReminder((id) => amApi.snoozeReminder(id, 1), "notify.snoozed")} disabled={busy}>
                {t("notify.snooze1")}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => onReminder(amApi.skipReminder, "notify.skipped")} disabled={busy}>
                {t("notify.skip")}
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => onReminder(amApi.stopReminder, "notify.stopped")} disabled={busy}>
                {t("notify.stop")}
              </Button>
            </>
          ) : null}
        </div>
      }
    >
      <span className="block font-medium text-text-primary">{n.title}</span>
      {n.body ? <span className="block">{n.body}</span> : null}
    </BannerStrip>
  );
}
