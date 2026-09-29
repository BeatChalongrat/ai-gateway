// AM6 — a reminder reaches someone who has closed the window.
//
// Closing the window hides it to the tray, and the page stops polling while
// it is hidden: the badge it would have shown reaches nobody. The host is
// still alive then and it holds the session, so every 60 s it asks chat-edge
// for the Agent Market notifications this surface has not shown, shows each
// ONCE as an OS notification, and tells the server it was shown.
//
// What it does not do. It builds no text: title and body are the server's
// sentences, shown as they came. It starts nothing: a click opens a page
// where a person acts. It never leaves the page's origin: the link is a path
// of the agents pages, checked here before use, and anything else opens the
// agents home. It has no settings: a person manages reminders in the page.
//
// Quiet by design. Not signed in → no request. 404 (Agent Market is off, or
// an older server) → no more requests until the app restarts. Any other
// failure → ask again at the next tick. Never a dialog, and one log line per
// change, not one per tick. Titles, bodies and links are not logged.
//
// No 'electron' import: the OS notification and the window come in as
// interfaces, so the decisions are tested in plain Node
// (test/am6-agent-notify.test.ts).

import type { Logger } from "./logger";

const SURFACE = "desktop";

/** Where a click lands when the link cannot be used. */
export const AGENTS_HOME = "/chat/agents";
const AGENTS_PREFIX = "/chat/agents/";
const MAX_LINK_LENGTH = 2048;
/** Stands in for the page's origin while a link is normalised; never requested. */
const NORMALISE_BASE = "https://page.invalid";

/**
 * The path to open for a notification's `link`, or AGENTS_HOME. A link is
 * used only when it is a path under /chat/agents/ with no scheme, no `//`,
 * no backslash and no control character, and is still under /chat/agents/
 * once its dot segments are resolved (the normal form is what is returned).
 * The server is trusted for the text it sends, not for where a click goes.
 */
export function safeAgentLink(link: unknown): string {
  if (typeof link !== "string" || link.length > MAX_LINK_LENGTH) return AGENTS_HOME;
  if (!link.startsWith(AGENTS_PREFIX)) return AGENTS_HOME;
  // The URL parser drops tab and newline silently and reads `\` as `/`, so
  // these are refused before it sees them.
  if (/[\p{Cc}\p{Zl}\p{Zp}\\]/u.test(link)) return AGENTS_HOME;
  if (link.includes("//") || /[a-z][a-z0-9+.-]*:/i.test(link)) return AGENTS_HOME;
  let parsed: URL;
  try {
    parsed = new URL(link, NORMALISE_BASE);
  } catch {
    return AGENTS_HOME;
  }
  if (parsed.origin !== NORMALISE_BASE || parsed.username !== "" || parsed.password !== "") return AGENTS_HOME;
  if (!parsed.pathname.startsWith(AGENTS_PREFIX)) return AGENTS_HOME;
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

/** The address a click loads: the page's own origin + the checked path. */
export function agentPageUrl(pageBase: string, link: unknown): string {
  return `${new URL(pageBase).origin}${safeAgentLink(link)}`;
}

export interface NotificationFeed {
  /** Null = no session (no request made); `status` 0 = no answer. */
  listAgentNotifications(surface: "desktop"): Promise<{ status: number; notifications: unknown[] } | null>;
  markAgentNotificationShown(id: string, surface: "desktop"): Promise<boolean>;
}
export interface Toaster {
  /** One OS notification. False = the OS could not show it. */
  show(title: string, body: string, onClick: () => void): boolean;
}
export interface MainWindowPort {
  /** Restore from the tray, show, focus. */
  show(): void;
  /** Load an address of the page's own origin in the main window. */
  load(url: string): void;
}

export interface AgentNotifierDeps {
  feed: NotificationFeed;
  toaster: Toaster;
  window: MainWindowPort;
  /** The origin the main window loads its page from. */
  pageOrigin: string;
  isSignedIn: () => boolean;
  log: Logger;
}

interface Shown {
  id: string;
  title: string;
  body: string;
  link: unknown;
}

/** The fields this host uses, or null for a row it cannot show. */
function notificationOf(row: unknown): Shown | null {
  if (typeof row !== "object" || row === null) return null;
  const r = row as Record<string, unknown>;
  const id = r["id"];
  const title = r["title"];
  const body = r["body"];
  if (typeof id !== "string" || id === "") return null;
  if (typeof title !== "string" || title === "" || typeof body !== "string") return null;
  return { id, title, body, link: r["link"] };
}

export class AgentNotifier {
  static readonly TICK_MS = 60_000;
  static readonly FIRST_DELAY_MS = 15_000; // let the session settle first
  static readonly SIGNED_IN_DELAY_MS = 3_000;
  /** A person who was away gets three notifications, not twenty at once. */
  static readonly MAX_PER_TICK = 3;

  private readonly origin: string;
  /** Shown by this process. A "shown" the server never received must not
   * show the same notification twice in one session of the app. */
  private readonly shown = new Set<string>();
  private interval: ReturnType<typeof setInterval> | null = null;
  private soon: ReturnType<typeof setTimeout> | null = null;
  private inFlight = false;
  /** The server has no such route: nothing is asked again until a restart. */
  private absent = false;
  private failing = false;

  constructor(private readonly deps: AgentNotifierDeps) {
    this.origin = new URL(deps.pageOrigin).origin;
  }

  start(): void {
    if (this.interval !== null || this.absent) return;
    this.deps.log.info(`AGENT NOTIFY started (${AgentNotifier.TICK_MS / 1000}s tick; asks only while signed in, window shown or not)`);
    this.askSoon(AgentNotifier.FIRST_DELAY_MS);
    this.interval = setInterval(() => void this.tick(), AgentNotifier.TICK_MS);
  }

  stop(): void {
    if (this.interval !== null) clearInterval(this.interval);
    if (this.soon !== null) clearTimeout(this.soon);
    this.interval = null;
    this.soon = null;
  }

  /** A sign-in just completed: ask now rather than at the next minute. */
  onSignedIn(): void {
    if (this.interval === null) return;
    this.askSoon(AgentNotifier.SIGNED_IN_DELAY_MS);
  }

  private askSoon(delayMs: number): void {
    if (this.soon !== null) clearTimeout(this.soon);
    this.soon = setTimeout(() => {
      this.soon = null;
      void this.tick();
    }, delayMs);
  }

  /** One poll cycle. Public for unit tests; the timers call it. Never throws. */
  async tick(): Promise<void> {
    if (this.absent || this.inFlight || !this.deps.isSignedIn()) return;
    this.inFlight = true;
    try {
      const answer = await this.deps.feed.listAgentNotifications(SURFACE);
      if (answer === null) return; // the session ended between the check and the request
      if (answer.status === 404) {
        this.absent = true;
        this.stop();
        this.deps.log.info("AGENT NOTIFY: this server has no Agent Market notifications (404) — not asking again until the app restarts");
        return;
      }
      if (answer.status !== 200) {
        this.noteFailure(answer.status === 0 ? "no answer" : `HTTP ${answer.status}`);
        return;
      }
      if (this.failing) {
        this.failing = false;
        this.deps.log.info("AGENT NOTIFY: the server answers again");
      }
      let shownNow = 0;
      let saidAgain = 0;
      for (const row of answer.notifications) {
        const n = notificationOf(row);
        if (n === null) continue;
        if (this.shown.has(n.id)) {
          // Shown here and still listed: the server never heard. Say it
          // again, so the next start of the app does not show it once more.
          if (saidAgain < AgentNotifier.MAX_PER_TICK) {
            saidAgain += 1;
            await this.deps.feed.markAgentNotificationShown(n.id, SURFACE);
          }
          continue;
        }
        if (shownNow >= AgentNotifier.MAX_PER_TICK) continue;
        if (!this.deps.toaster.show(n.title, n.body, () => this.open(n.link))) break;
        this.shown.add(n.id);
        shownNow += 1;
        await this.deps.feed.markAgentNotificationShown(n.id, SURFACE);
      }
      if (shownNow > 0) this.deps.log.info(`AGENT NOTIFY: ${shownNow} notification(s) shown`);
    } catch (err) {
      this.noteFailure((err as Error).message);
    } finally {
      this.inFlight = false;
    }
  }

  /** The first failure of a run is logged; the ones after it are not. */
  private noteFailure(detail: string): void {
    if (this.failing) return;
    this.failing = true;
    this.deps.log.warn(`AGENT NOTIFY: could not ask for notifications (${detail}) — asking again at the next tick`);
  }

  private open(link: unknown): void {
    try {
      const path = safeAgentLink(link);
      if (path === AGENTS_HOME) this.deps.log.warn("AGENT NOTIFY: the notification's link is not a page of the agents — opening the agents home");
      else this.deps.log.info("AGENT NOTIFY: opening the notification's page");
      this.deps.window.show();
      this.deps.window.load(`${this.origin}${path}`);
    } catch (err) {
      this.deps.log.warn(`AGENT NOTIFY: could not open the page (${(err as Error).message})`);
    }
  }
}
