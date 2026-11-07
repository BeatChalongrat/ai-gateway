// FIX41 Part F — the desktop run ledger that outlives the process.
//
// THE PROBLEM IT SOLVES (probe P5). The host's event log lived in a Map in
// the main process. Kill the app mid-turn and the whole turn vanished: the
// replay buffer went with the process, and — worse — `importVisible` and
// `saveLocalHistory` only run AFTER runLoop returns, so neither the
// question nor the half-written answer was ever written anywhere. The user
// came back to a conversation that had never heard of the turn.
//
// WHAT THIS DOES. Every frame the host emits is appended to a JSONL file
// per run, next to conversation-state. On the next start, a run whose file
// has no terminal line is settled: the page is told the turn was
// interrupted rather than being left to wonder. Files are removed once
// their run has ended and aged past the TTL.
//
// WHAT IT DOES NOT DO. The generation is not resumed — the process that
// held the gateway connection is gone. Same boundary as the server side.
//
// PRIVACY (see docs/OPEN_DECISIONS.md #22). These files contain assistant
// text in the clear, exactly like conversation-state/*.json already does.
// FIX41 follows the existing trust boundary rather than inventing a new
// one, and the report lists precisely what it adds to the user's disk so
// the encryption question can be decided once, for all of it, by the owner.
//
// No 'electron' import: unit-tested in plain Node.

import fs from "node:fs";
import path from "node:path";

export interface LedgerLine {
  seq: number;
  event: string;
  data: unknown;
}

export interface PendingRun {
  conversationId: string;
  runId: string;
  lastSeq: number;
  /** The events recorded for the run, oldest first. */
  events: LedgerLine[];
}

const TERMINALS = new Set(["message_complete", "error", "cancelled"]);

/** Default: the same ten-minute window the server keeps (STREAM_CONTRACT §8). */
export const RUN_LEDGER_TTL_MS = 600_000;

export class RunLedgerStore {
  constructor(
    private readonly dir: string,
    private readonly ttlMs: number = RUN_LEDGER_TTL_MS,
  ) {}

  private file(runId: string): string {
    return path.join(this.dir, `${runId.replace(/[^A-Za-z0-9_-]/g, "_")}.jsonl`);
  }

  /** Called once per run, before any append. */
  begin(conversationId: string, runId: string): void {
    try {
      fs.mkdirSync(this.dir, { recursive: true });
      fs.writeFileSync(
        this.file(runId),
        JSON.stringify({ type: "run", conversationId, runId, startedAt: new Date().toISOString() }) + "\n",
      );
    } catch {
      /* a ledger that cannot be written must never stop a turn */
    }
  }

  /** Append one frame. Best-effort by design: bookkeeping never breaks a turn. */
  append(runId: string, line: LedgerLine): void {
    try {
      fs.appendFileSync(this.file(runId), JSON.stringify(line) + "\n");
    } catch {
      /* ignored */
    }
  }

  /** Mark a run finished. The file is kept for the TTL so a page that
   *  reloads just after the end still finds the ending. */
  end(runId: string): void {
    try {
      fs.appendFileSync(this.file(runId), JSON.stringify({ type: "end", endedAt: new Date().toISOString() }) + "\n");
    } catch {
      /* ignored */
    }
  }

  forget(runId: string): void {
    try {
      fs.rmSync(this.file(runId), { force: true });
    } catch {
      /* ignored */
    }
  }

  /**
   * Runs that were still in flight when the process went away: their file
   * has frames but no terminal one. Returned oldest-first so the caller
   * settles them in the order they happened.
   */
  pending(): PendingRun[] {
    let names: string[];
    try {
      names = fs.readdirSync(this.dir).filter((n) => n.endsWith(".jsonl"));
    } catch {
      return [];
    }
    const out: PendingRun[] = [];
    for (const name of names) {
      const p = path.join(this.dir, name);
      let raw: string;
      let mtime = 0;
      try {
        raw = fs.readFileSync(p, "utf8");
        mtime = fs.statSync(p).mtimeMs;
      } catch {
        continue;
      }
      let conversationId = "";
      let runId = "";
      let terminated = false;
      let ended = false;
      const events: LedgerLine[] = [];
      for (const l of raw.split("\n")) {
        if (!l.trim()) continue;
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(l) as Record<string, unknown>;
        } catch {
          continue; // a torn last line — the process died mid-write
        }
        if (parsed["type"] === "run") {
          conversationId = String(parsed["conversationId"] ?? "");
          runId = String(parsed["runId"] ?? "");
          continue;
        }
        if (parsed["type"] === "end") {
          ended = true;
          continue;
        }
        if (typeof parsed["seq"] !== "number" || typeof parsed["event"] !== "string") continue;
        events.push({ seq: parsed["seq"], event: parsed["event"], data: parsed["data"] });
        if (TERMINALS.has(parsed["event"])) terminated = true;
      }
      const expired = Date.now() - mtime > this.ttlMs;
      if (terminated || ended || !conversationId || !runId || events.length === 0) {
        // Finished, or unreadable. Reap it once it is past the window.
        if (expired || terminated || ended) {
          if (expired) fs.rmSync(p, { force: true });
        }
        continue;
      }
      if (expired) {
        // Old AND unterminated. Nothing is watching a turn from ten
        // minutes ago; drop it rather than replaying it into a page that
        // has moved on.
        fs.rmSync(p, { force: true });
        continue;
      }
      out.push({
        conversationId,
        runId,
        lastSeq: events[events.length - 1]?.seq ?? 0,
        events,
      });
    }
    return out;
  }

  /** Delete every finished run whose file is older than the TTL. */
  sweep(): number {
    let removed = 0;
    let names: string[];
    try {
      names = fs.readdirSync(this.dir).filter((n) => n.endsWith(".jsonl"));
    } catch {
      return 0;
    }
    for (const name of names) {
      const p = path.join(this.dir, name);
      try {
        if (Date.now() - fs.statSync(p).mtimeMs > this.ttlMs) {
          fs.rmSync(p, { force: true });
          removed++;
        }
      } catch {
        /* ignored */
      }
    }
    return removed;
  }
}
