// FIX127 C2 — a finished run is not replayed to a page that was not following it.
//
// e49 / R3: reopening a desktop chat showed the last answer twice. The page
// loads the transcript from the server, then asks the host `ui.ready` with
// no cursor (a fresh page after a hard navigation, F5, re-auth, a deep
// link); the host replayed the whole ended run, and the page painted a
// second copy from frames alone — without the row's files, which is also
// why the plan card offered "Generate Document" again (C1).
//
// A page that WAS following the run (same run id, a cursor behind the
// terminal) still gets the tail it missed, terminal included.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { GrantStore } from "../src/main/grants";
import { PermissionStore } from "../src/main/permissions";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import type { Logger } from "../src/main/logger";

const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };

interface Posted {
  conv: string;
  runId: string | undefined;
  seq: number;
  event: string;
}

function build() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix127-"));
  const posted: Posted[] = [];
  const bridge = {
    postTurnEvent: (conv: string, seq: number, event: string, _data: unknown, runId?: string) => posted.push({ conv, seq, event, runId }),
    post: () => {},
    postHostResult: () => {},
    postHostError: () => {},
  };
  const co = new TurnCoordinator(
    bridge as never, {} as never, {} as never, {} as never, {} as never, {} as never,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null },
    log,
  );
  return { co, posted };
}

type Internals = { beginRun(c: string): string; emit(c: string, e: string, d: unknown): number };
const inner = (co: TurnCoordinator) => co as unknown as Internals;

function finishedRun(co: TurnCoordinator) {
  const runId = inner(co).beginRun("c1");
  inner(co).emit("c1", "message_start", { message_id: "asst_1" });
  inner(co).emit("c1", "content_delta", { delta: "the answer" });
  inner(co).emit("c1", "message_complete", { message_id: "asst_1" });
  return runId;
}

describe("FIX127 C2 — reopen does not replay an ended run", () => {
  it("a fresh page (no cursor) after the run ended gets nothing: the server transcript has the answer", () => {
    const { co, posted } = build();
    finishedRun(co);
    posted.length = 0;

    co.onUiReady({ conversationId: "c1", lastSeq: 0 });

    expect(posted).toEqual([]);
  });

  it("a page with another run's cursor after the run ended gets nothing either", () => {
    const { co, posted } = build();
    finishedRun(co);
    posted.length = 0;

    co.onUiReady({ conversationId: "c1", lastSeq: 7, runId: "some-older-run" });

    expect(posted).toEqual([]);
  });

  it("a page that was following the run and missed its tail still gets the tail, terminal included", () => {
    const { co, posted } = build();
    const runId = finishedRun(co);
    posted.length = 0;

    co.onUiReady({ conversationId: "c1", lastSeq: 1, runId });

    expect(posted.map((p) => p.event)).toEqual(["content_delta", "message_complete"]);
    expect(posted.every((p) => p.runId === runId)).toBe(true);
  });

  it("a LIVE run (no terminal yet) is still replayed in full to a fresh page", () => {
    const { co, posted } = build();
    inner(co).beginRun("c1");
    inner(co).emit("c1", "message_start", { message_id: "asst_2" });
    inner(co).emit("c1", "content_delta", { delta: "still writing" });
    posted.length = 0;

    co.onUiReady({ conversationId: "c1", lastSeq: 0 });

    expect(posted.map((p) => p.event)).toEqual(["message_start", "content_delta"]);
  });
});
