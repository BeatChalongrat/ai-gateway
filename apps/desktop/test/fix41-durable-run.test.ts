// FIX41 — the desktop half of docs/STREAM_CONTRACT.md.
//
// Covers Part D (run_id on every frame, `cancelled` as a real terminal, a
// terminal on every path, the two-turn cursor question) and the desktop
// probes P5/P6.
//
// These drive the REAL TurnCoordinator event machinery (emit / beginRun /
// onUiReady) against a fake bridge, plus the REAL page-side cursor rule
// copied from apps/web-chat/src/lib/bridge.ts so the two halves are
// checked against each other rather than each against its own assumption.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };

interface Posted {
  conv: string;
  runId: string | undefined;
  seq: number;
  event: string;
  data: unknown;
}

function build() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix41-"));
  const posted: Posted[] = [];
  const bridge = {
    postTurnEvent: (conv: string, seq: number, event: string, data: unknown, runId?: string) =>
      posted.push({ conv, seq, event, data, runId }),
    post: () => {},
    postHostResult: () => {},
    postHostError: () => {},
  };
  const co = new TurnCoordinator(
    bridge as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null },
    log,
  );
  return { co, posted, tmp };
}

// The page's delivery rule, verbatim from apps/web-chat/src/lib/bridge.ts.
// If the host and the page ever disagree about what advances a cursor, this
// is where it shows up.
function makePageCursor() {
  const lastSeq = new Map<string, number>();
  const lastRunId = new Map<string, string>();
  const delivered: Posted[] = [];
  return {
    delivered,
    /** Returns true when the frame reaches the renderer. */
    receive(f: Posted): boolean {
      const known = lastRunId.get(f.conv);
      if (f.runId && known && f.runId !== known) lastSeq.delete(f.conv);
      if (f.seq <= (lastSeq.get(f.conv) ?? 0)) return false;
      lastSeq.set(f.conv, f.seq);
      if (f.runId) lastRunId.set(f.conv, f.runId);
      delivered.push(f);
      return true;
    },
    onSubmit(conv: string) {
      lastSeq.delete(conv);
      lastRunId.delete(conv);
    },
    seq: (conv: string) => lastSeq.get(conv) ?? 0,
  };
}

type Internals = {
  beginRun(conversationId: string): string;
  emit(conversationId: string, event: string, data: unknown): number;
  emitTerminalIfMissing(conversationId: string, err: { code: string; message: string }): void;
};
const inner = (co: TurnCoordinator) => co as unknown as Internals;

describe("FIX41 — the run envelope on the desktop host", () => {
  it("every frame carries the run id, and seq starts at 1 for the first run", () => {
    const { co, posted } = build();
    const runId = inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", { message_id: "a1" });
    inner(co).emit("c1", "content_delta", { delta: "hi" });
    inner(co).emit("c1", "message_complete", { message_id: "a1" });

    expect(posted.map((p) => p.event)).toEqual(["message_start", "content_delta", "message_complete"]);
    expect(posted.map((p) => p.seq)).toEqual([1, 2, 3]);
    for (const p of posted) expect(p.runId).toBe(runId);
  });

  it("a second turn gets a NEW run id, and seq is carried forward not reset", () => {
    const { co, posted } = build();
    const runA = inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "message_complete", {});
    const runB = inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});

    expect(runB).not.toBe(runA);
    expect(posted.map((p) => p.seq)).toEqual([1, 2, 3]);
    expect(posted[2].runId).toBe(runB);
  });
});

// D3 — the brief expected this to be broken: "seq resets to 1 while the
// page's cursor is still at 40, so the second turn's frames are all
// dropped". It is NOT broken, and this pins WHY, so it stays that way.
describe("FIX41 D3 — two turns in one conversation", () => {
  it("the second turn's frames all reach the page", () => {
    const { co, posted } = build();
    const page = makePageCursor();

    page.onSubmit("c1");
    inner(co).beginRun("c1");
    for (let i = 0; i < 40; i++) inner(co).emit("c1", "content_delta", { delta: "x" });
    inner(co).emit("c1", "message_complete", {});
    for (const f of posted) page.receive(f);
    expect(page.seq("c1")).toBe(41);

    // Turn two.
    const before = posted.length;
    page.onSubmit("c1");
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "second answer" });
    inner(co).emit("c1", "message_complete", {});
    const second = posted.slice(before);
    for (const f of second) expect(page.receive(f)).toBe(true);
    expect(page.delivered.filter((d) => d.data && (d.data as { delta?: string }).delta === "second answer")).toHaveLength(1);
  });

  it("...even if the page never cleared its cursor, because the run id changed", () => {
    const { co, posted } = build();
    const page = makePageCursor();

    inner(co).beginRun("c1");
    for (let i = 0; i < 40; i++) inner(co).emit("c1", "content_delta", { delta: "x" });
    for (const f of posted) page.receive(f);
    expect(page.seq("c1")).toBe(40);

    // Second run whose host-side seq restarts low — which is exactly what a
    // ledger reloaded from disk after a restart can produce. WITHOUT the run
    // id in the envelope every one of these frames would be dropped.
    const before = posted.length;
    inner(co).beginRun("c1");
    const log = (co as unknown as { logs: Map<string, { seq: number }> }).logs.get("c1")!;
    log.seq = 0; // simulate a fresh process's counter
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "after a restart" });
    for (const f of posted.slice(before)) expect(page.receive(f)).toBe(true);
    expect(page.seq("c1")).toBe(2);
  });
});

describe("FIX41 D2 — however it ends, it says so", () => {
  it("emitTerminalIfMissing closes a run that produced no terminal", () => {
    const { co, posted } = build();
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "half" });

    inner(co).emitTerminalIfMissing("c1", { code: "host_exception", message: "boom" });

    const last = posted[posted.length - 1];
    expect(last.event).toBe("error");
    expect((last.data as { code: string }).code).toBe("host_exception");
  });

  it("it does NOT append a second terminal to a run that ended properly", () => {
    const { co, posted } = build();
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "message_complete", {});
    const n = posted.length;

    inner(co).emitTerminalIfMissing("c1", { code: "no_result", message: "x" });
    expect(posted).toHaveLength(n);
  });

  it("it stays silent for a conversation that never opened a run", () => {
    const { co, posted } = build();
    inner(co).emitTerminalIfMissing("never-started", { code: "no_result", message: "x" });
    expect(posted).toHaveLength(0);
  });

  it("a cancelled run closes the page's sink, like message_complete does", () => {
    // Mirrors the page rule: all THREE terminals end the stream.
    const terminal = (e: string) => e === "message_complete" || e === "error" || e === "cancelled";
    expect(terminal("cancelled")).toBe(true);

    const { co, posted } = build();
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "cancelled", { message_id: "a1", partial: true });
    const last = posted[posted.length - 1];
    expect(last.event).toBe("cancelled");
    expect((last.data as { partial: boolean }).partial).toBe(true);
  });
});

// P6 — reload the page mid-turn. This worked before FIX41 and must keep
// working; the only change is that the replay now names its run.
describe("FIX41 P6 — reload mid-turn (must not regress)", () => {
  it("replays exactly the frames after the page's cursor, under the same run id", () => {
    const { co, posted } = build();
    const runId = inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "one " });
    inner(co).emit("c1", "content_delta", { delta: "two " });
    inner(co).emit("c1", "content_delta", { delta: "three" });
    const live = posted.length;

    // The page reloaded having rendered up to seq 2.
    co.onUiReady({ conversationId: "c1", lastSeq: 2, runId });
    const replay = posted.slice(live);
    expect(replay.map((p) => p.seq)).toEqual([3, 4]);
    for (const p of replay) expect(p.runId).toBe(runId);
  });

  it("a cursor from ANOTHER run replays the whole current run, not nothing", () => {
    const { co, posted } = build();
    const runId = inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "one" });
    const live = posted.length;

    // A stale cursor whose number is ahead of this run. Trusting it would
    // withhold the beginning of the answer the user is waiting for.
    co.onUiReady({ conversationId: "c1", lastSeq: 99, runId: "some-other-run" });
    const replay = posted.slice(live);
    expect(replay.map((p) => p.seq)).toEqual([1, 2]);
  });

  it("a page with no run id keeps the pre-FIX41 behaviour", () => {
    const { co, posted } = build();
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", {});
    inner(co).emit("c1", "content_delta", { delta: "one" });
    const live = posted.length;

    co.onUiReady({ conversationId: "c1", lastSeq: 1, runId: null });
    expect(posted.slice(live).map((p) => p.seq)).toEqual([2]);
  });
});

// P5 — close the app mid-turn, open it again.
//
// Before FIX41 Part F the host's event log was a Map in the main process:
// the turn vanished with the process, and because importVisible /
// saveLocalHistory only run after runLoop returns, neither the question nor
// the half-written answer was recorded anywhere. The user came back to a
// conversation that had never heard of the turn.
describe("FIX41 P5 — the app is killed mid-turn", () => {
  function ledgerDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix41-ledger-"));
  }

  it("a run interrupted by the process going away is recoverable and CLOSED on the next start", async () => {
    const { RunLedgerStore } = await import("../src/main/run-ledger");
    const dir = ledgerDir();

    // ── session one: a turn that never finishes ──────────────────────────
    const first = build();
    first.co.setRunLedger(new RunLedgerStore(dir));
    const runId = inner(first.co).beginRun("c1");
    inner(first.co).emit("c1", "message_start", { message_id: "a1" });
    inner(first.co).emit("c1", "content_delta", { delta: "คำตอบครึ่งเดียว" });
    // ...and the process dies here. No terminal is ever written.

    // ── session two: a brand new coordinator over the same directory ─────
    const second = build();
    second.co.setRunLedger(new RunLedgerStore(dir));
    const settled = second.co.settleInterruptedRuns();

    expect(settled).toBe(1);
    // The page is handed what was produced AND an honest ending.
    const events = second.posted.map((p) => p.event);
    expect(events[events.length - 1]).toBe("error");
    const err = second.posted[second.posted.length - 1];
    expect((err.data as { code: string }).code).toBe("generation_interrupted");
    expect(err.runId).toBe(runId);
    // seq continues from where the interrupted run stopped, so a page that
    // had rendered up to 2 does not re-render them.
    expect(err.seq).toBe(3);

    // And the recovered frames are replayable, so a page that reloads gets
    // the answer-so-far rather than a blank bubble.
    const before = second.posted.length;
    second.co.onUiReady({ conversationId: "c1", lastSeq: 0, runId });
    expect(second.posted.slice(before).map((p) => p.event)).toEqual([
      "message_start", "content_delta", "error",
    ]);
  });

  it("a run that ENDED properly is not resurrected", async () => {
    const { RunLedgerStore } = await import("../src/main/run-ledger");
    const dir = ledgerDir();
    const first = build();
    first.co.setRunLedger(new RunLedgerStore(dir));
    inner(first.co).beginRun("c1");
    inner(first.co).emit("c1", "message_start", {});
    inner(first.co).emit("c1", "message_complete", {});

    const second = build();
    second.co.setRunLedger(new RunLedgerStore(dir));
    expect(second.co.settleInterruptedRuns()).toBe(0);
    expect(second.posted).toHaveLength(0);
  });

  it("an interrupted run older than the TTL is reaped, not replayed", async () => {
    const { RunLedgerStore } = await import("../src/main/run-ledger");
    const dir = ledgerDir();
    const first = build();
    first.co.setRunLedger(new RunLedgerStore(dir));
    inner(first.co).beginRun("c1");
    inner(first.co).emit("c1", "message_start", {});
    inner(first.co).emit("c1", "content_delta", { delta: "stale" });

    // Nothing is watching a turn from ten minutes ago.
    const second = build();
    second.co.setRunLedger(new RunLedgerStore(dir, 0));
    expect(second.co.settleInterruptedRuns()).toBe(0);
    expect(second.posted).toHaveLength(0);
    expect(fs.readdirSync(dir).filter((n) => n.endsWith(".jsonl"))).toHaveLength(0);
  });

  it("a torn last line (the process died mid-write) does not lose the rest", async () => {
    const { RunLedgerStore } = await import("../src/main/run-ledger");
    const dir = ledgerDir();
    const first = build();
    first.co.setRunLedger(new RunLedgerStore(dir));
    const runId = inner(first.co).beginRun("c1");
    inner(first.co).emit("c1", "message_start", {});
    inner(first.co).emit("c1", "content_delta", { delta: "kept" });
    const file = path.join(dir, `${runId}.jsonl`);
    fs.appendFileSync(file, '{"seq":3,"event":"content_de');

    const store = new RunLedgerStore(dir);
    const pending = store.pending();
    expect(pending).toHaveLength(1);
    expect(pending[0].events.map((e) => e.event)).toEqual(["message_start", "content_delta"]);
  });

  it("a ledger directory that cannot be read never breaks a turn", async () => {
    const { RunLedgerStore } = await import("../src/main/run-ledger");
    const store = new RunLedgerStore(path.join(os.tmpdir(), "sran-fix41-does-not-exist", "nested"));
    expect(store.pending()).toEqual([]);
    expect(store.sweep()).toBe(0);
    // Appending to a run that was never begun must not throw.
    expect(() => store.append("no-such-run", { seq: 1, event: "x", data: {} })).not.toThrow();
  });
});
