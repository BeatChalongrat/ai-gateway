// FIX2 §1.1 — the scheduled-task firing timer, in the HOST.
//
// The WPF app's TaskSchedulerService is a 60 s System.Threading.Timer that fires
// due tasks by running their prompt through the chat engine. In the Electron
// shape the firing MUST be host-side: only the host has the turn engine
// (TurnCoordinator), the gateway client, and the session — the sidecar has none
// of these by design. So the sidecar keeps the STORE (and answers `tasks._due`),
// and this host timer polls it and runs each due task as a turn.
//
// It fires only while this process is alive (the app open, incl. minimized to
// tray). That truth is stated in the Tasks UI — the timer does not run in the
// user's absence. Whether scheduled work should move server-side is the owner's
// pending decision, not built here.

import type { Logger } from "./logger";
import type { SidecarClient } from "./sidecar-client";

const TICK_MS = 60_000;
const FIRST_DELAY_MS = 20_000; // let the sidecar + session settle first

export interface DueTask {
  id: string;
  name: string;
  prompt: string;
  conversationId?: string | null;
  permissionMode?: string;
}
/** FIX8 A1.1: a missed-run catch-up — the ONE most-recent missed occurrence and how
 * many older ones were discarded (recorded as skips sidecar-side). */
export interface CatchUpTask extends DueTask {
  catchUpFor: string;
  skippedOlder: number;
}

export interface Firer {
  /** Run a task's prompt as a turn. `opts.outcome`/`scheduledFor` label the run in
   * history; `permissionMode` governs its approvals (A1.3). */
  fireTask(task: DueTask, opts?: { outcome?: "ok" | "catchup"; scheduledFor?: string | null }): Promise<void>;
}

/** FIX8 A1.1: how the host tells the user a catch-up started (a toast/status). */
export type CatchUpNotifier = (task: CatchUpTask) => void;

export class TaskScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  /** The task currently running (null = idle). One at a time: a slow AI run can
   * outlast a tick, and only one turn runs at once. Used to attribute skips. */
  private runningTaskId: string | null = null;
  private caughtUp = false;

  constructor(
    private readonly sidecar: SidecarClient,
    private readonly firer: Firer,
    private readonly isSignedIn: () => boolean,
    private readonly log: Logger,
    private readonly notifyCatchUp: CatchUpNotifier = () => {},
  ) {}

  start(): void {
    if (this.timer) return;
    this.log.info(`SCHEDULER started (${TICK_MS / 1000}s tick; fires only while the app is running)`);
    this.timer = setTimeout(() => void this.loop(), FIRST_DELAY_MS);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** FIX8 A1.1: run the missed-run catch-up pass — call on app start AND on machine
   * wake (Electron powerMonitor 'resume'). The sidecar decides the ONE most-recent
   * missed occurrence per task (≤7 days) and records the older ones as skipped; the
   * host fires that one catch-up and notifies. Idempotent-ish: safe to call again on
   * wake (a task with nothing missed is not returned). */
  async catchUpMissed(): Promise<void> {
    if (this.stopped || !this.isSignedIn()) return;
    try {
      const r = await this.sidecar.hostQuery("tasks._catchUp", {});
      const tasks = (r.data as { tasks?: CatchUpTask[] } | undefined)?.tasks ?? [];
      for (const task of tasks) {
        if (this.stopped) break;
        this.log.info(`SCHEDULER catch-up: ${task.name} missed ${task.catchUpFor} (+${task.skippedOlder} older discarded)`);
        this.notifyCatchUp(task);
        this.runningTaskId = task.id;
        try {
          await this.firer.fireTask(task, { outcome: "catchup", scheduledFor: task.catchUpFor });
        } finally {
          this.runningTaskId = null;
        }
      }
    } catch (err) {
      this.log.warn(`SCHEDULER catch-up failed: ${(err as Error).message}`);
    }
  }

  private async loop(): Promise<void> {
    if (this.stopped) return;
    if (!this.caughtUp && this.isSignedIn()) {
      this.caughtUp = true;
      await this.catchUpMissed(); // once, at first ready tick
    }
    await this.tick();
    if (!this.stopped) this.timer = setTimeout(() => void this.loop(), TICK_MS);
  }

  /** One poll cycle. Public for unit tests; the loop calls it on the tick. */
  async tick(): Promise<void> {
    if (!this.isSignedIn()) return; // a task's turn needs the session
    try {
      const r = await this.sidecar.hostQuery("tasks._due", {});
      const due = (r.data as { tasks?: DueTask[] } | undefined)?.tasks ?? [];
      if (!due.length) return;

      // FIX8 A1.2: a run from a prior tick may still be going (an AI turn can
      // outlast the 60 s tick). Rather than silently drop the due tasks, RECORD a
      // skip with the right reason — this task's own previous run still going
      // (prev_running) or another task already running (others_running).
      if (this.runningTaskId !== null) {
        for (const task of due) {
          const reason = task.id === this.runningTaskId ? "prev_running" : "others_running";
          await this.sidecar.hostQuery("tasks._recordSkip", { taskId: task.id, reason });
          this.log.info(`SCHEDULER: skipped ${task.name} (${reason})`);
        }
        return;
      }

      this.log.info(`SCHEDULER: ${due.length} task(s) due`);
      for (const task of due) {
        if (this.stopped) break;
        this.runningTaskId = task.id;
        try {
          await this.firer.fireTask(task, { outcome: "ok", scheduledFor: null });
        } finally {
          this.runningTaskId = null;
        }
      }
    } catch (err) {
      this.log.warn(`SCHEDULER tick failed: ${(err as Error).message}`);
    }
  }
}
