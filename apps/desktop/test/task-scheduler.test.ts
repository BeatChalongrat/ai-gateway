// FIX8 A1 — the HOST half of the scheduler: the missed-run catch-up orchestration
// (A1.1) and the skip-reason decision (A1.2). Driven with a fake sidecar + fake
// firer, so the DECISIONS are proven without a live turn (the live firing is
// owner-gated on a signed-in session).
import { describe, expect, it, vi } from "vitest";

import { TaskScheduler, type CatchUpTask, type DueTask } from "../src/main/task-scheduler";
import type { SidecarClient } from "../src/main/sidecar-client";
import type { Logger } from "../src/main/logger";

const noopLog: Logger = { info: () => {}, warn: () => {}, error: () => {} };

/** A sidecar stub whose hostQuery is scripted per op, recording every call. */
function fakeSidecar(responses: Record<string, unknown>): { sidecar: SidecarClient; calls: { op: string; args: unknown }[] } {
  const calls: { op: string; args: unknown }[] = [];
  const sidecar = {
    hostQuery: async (op: string, args?: unknown) => {
      calls.push({ op, args });
      return { ok: true, data: responses[op] ?? {} };
    },
  } as unknown as SidecarClient;
  return { sidecar, calls };
}

describe("FIX8 A1.1 — missed-run catch-up orchestration", () => {
  it("fires exactly the ONE catch-up the sidecar returns, as a catchup, and notifies", async () => {
    const catchUp: CatchUpTask = { id: "t1", name: "Briefing", prompt: "p", catchUpFor: "2026-08-25T06:00:00Z", skippedOlder: 3 };
    const { sidecar } = fakeSidecar({ "tasks._catchUp": { tasks: [catchUp] } });
    const fired: { task: DueTask; opts: unknown }[] = [];
    const notified: CatchUpTask[] = [];
    const scheduler = new TaskScheduler(
      sidecar,
      { fireTask: async (task, opts) => { fired.push({ task, opts }); } },
      () => true,
      noopLog,
      (t) => notified.push(t),
    );

    await scheduler.catchUpMissed();

    expect(fired).toHaveLength(1);
    expect(fired[0]!.task.id).toBe("t1");
    expect(fired[0]!.opts).toEqual({ outcome: "catchup", scheduledFor: "2026-08-25T06:00:00Z" });
    expect(notified).toEqual([catchUp]); // the user is told a catch-up started (A1.1)
  });

  it("does nothing when signed out (a task's turn needs the session)", async () => {
    const { sidecar, calls } = fakeSidecar({ "tasks._catchUp": { tasks: [{ id: "t1" }] } });
    const scheduler = new TaskScheduler(sidecar, { fireTask: async () => {} }, () => false, noopLog);
    await scheduler.catchUpMissed();
    expect(calls).toHaveLength(0);
  });
});

describe("FIX8 A1.2 — a run that cannot fire is SKIPPED with the right reason", () => {
  it("records prev_running vs others_running while another run is in progress", async () => {
    // A catch-up whose fireTask never resolves keeps a run "in progress".
    let release!: () => void;
    const hang = new Promise<void>((r) => (release = r));
    const { sidecar, calls } = fakeSidecar({
      "tasks._catchUp": { tasks: [{ id: "running", name: "R", prompt: "p", catchUpFor: "2026-08-25T06:00:00Z", skippedOlder: 0 }] },
      "tasks._due": { tasks: [{ id: "running", name: "R", prompt: "p" }, { id: "other", name: "O", prompt: "p" }] },
    });
    const scheduler = new TaskScheduler(sidecar, { fireTask: () => hang }, () => true, noopLog);

    void scheduler.catchUpMissed(); // starts firing "running" and HANGS (runningTaskId = "running")
    await Promise.resolve();
    await scheduler.tick();          // due tasks cannot run — they must be SKIPPED with a reason

    const skips = calls.filter((c) => c.op === "tasks._recordSkip").map((c) => c.args as { taskId: string; reason: string });
    expect(skips).toContainEqual({ taskId: "running", reason: "prev_running" });   // THIS task's run still going
    expect(skips).toContainEqual({ taskId: "other", reason: "others_running" });   // a DIFFERENT task is running
    release();
  });

  it("fires due tasks normally when nothing is running", async () => {
    const { sidecar, calls } = fakeSidecar({ "tasks._due": { tasks: [{ id: "t1", name: "T", prompt: "p" }] } });
    const fired: string[] = [];
    const scheduler = new TaskScheduler(sidecar, { fireTask: async (t) => { fired.push(t.id); } }, () => true, noopLog);
    await scheduler.tick();
    expect(fired).toEqual(["t1"]);
    expect(calls.some((c) => c.op === "tasks._recordSkip")).toBe(false);
  });
});
