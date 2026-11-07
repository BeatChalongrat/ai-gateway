// Local-data hygiene: the wipe command and the conversation-state TTL
// sweep (docs/DATA_RETENTION_PLAN.md item 5).
//
// The desktop keeps plaintext derived state under <userData>:
// history/*.json (FIX109 Part C — the per-conversation gateway chain:
// user and assistant text plus tool calls and tool results, the one copy
// of the tool chains), conversation-state/*.json (per-conversation
// structured summaries), run-ledger/ (per-turn JSONL, already TTL'd
// in-store for completed runs), and logs/shell.log. All of it is
// re-derivable from the server — a missing history file makes the next
// turn read the server transcript (turn-coordinator.ts buildMessages),
// losing only the tool chains — but until now none of it could be removed
// from inside the app — only an uninstall cleared it.
//
// Two mechanisms:
//   * wipeLocalData — the explicit "Clear local data" action (tray
//     menu, behind a confirm dialog). Removes history, conversation-state,
//     run-ledger and logs wholesale. Counts only in the result —
//     never file contents.
//   * sweepConversationStateTTL / sweepHistoryTTL — delete
//     conversation-state and history files whose mtime is older than a
//     per-preference window (localStateTtlDays). Run at boot; 30 days by
//     default, 0 to disable. Unlike the server's deleting jobs these ship
//     ON: "off" on a server means data sits in a database the operator
//     controls, while here it means transcripts accumulate in plaintext
//     on a laptop forever. The cache is a convenience, not the record —
//     the conversation lives on the server — so the safe default is the
//     one that forgets. Until FIX109 the transcripts this sentence was
//     written about — history/ — were swept by neither mechanism
//     (measured: 244 files, 72 of them for conversations already deleted
//     on the server). A history file is rewritten on every desktop turn,
//     so an active conversation's chain never ages out under it.
//
// Encryption-at-rest for these files was considered and dropped: OS
// disk encryption already covers the threat it would address, and a
// TTL removes the data instead of protecting it in place.
//
// No 'electron' import: unit-tested in plain Node.

import fs from "node:fs";
import path from "node:path";

export interface WipeResult {
  /** FIX109 Part C: history/*.json — the transcripts with tool chains. */
  historyFiles: number;
  conversationStateFiles: number;
  runLedgerFiles: number;
  logFiles: number;
}

function countFiles(dir: string): number {
  try {
    return fs.readdirSync(dir).length;
  } catch {
    return 0;
  }
}

function removeDirContents(dir: string): number {
  const n = countFiles(dir);
  if (n > 0 || fs.existsSync(dir)) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      return 0;
    }
  }
  return n;
}

/** Remove history, conversation-state, run-ledger and shell logs under
 *  `userDataDir`. Returns per-kind file counts (never contents). */
export function wipeLocalData(userDataDir: string): WipeResult {
  return {
    historyFiles: removeDirContents(path.join(userDataDir, "history")),
    conversationStateFiles: removeDirContents(
      path.join(userDataDir, "conversation-state"),
    ),
    runLedgerFiles: removeDirContents(path.join(userDataDir, "run-ledger")),
    logFiles: removeDirContents(path.join(userDataDir, "logs")),
  };
}

/** Delete conversation-state JSON files not modified in `ttlDays`
 *  days. `ttlDays <= 0` means the sweep is OFF (the default) and
 *  nothing is touched. Returns the number of files removed. */
export function sweepConversationStateTTL(
  userDataDir: string,
  ttlDays: number,
  now: Date = new Date(),
): number {
  return sweepJsonTTL(path.join(userDataDir, "conversation-state"), ttlDays, now);
}

/** FIX109 Part C: the same sweep over history/*.json — the per-conversation
 *  chains. A swept chain costs nothing but its tool chains: the next turn
 *  reads the server transcript. Same window, same preference, same
 *  `ttlDays <= 0` = off. Returns the number of files removed. */
export function sweepHistoryTTL(
  userDataDir: string,
  ttlDays: number,
  now: Date = new Date(),
): number {
  return sweepJsonTTL(path.join(userDataDir, "history"), ttlDays, now);
}

function sweepJsonTTL(dir: string, ttlDays: number, now: Date): number {
  if (!Number.isFinite(ttlDays) || ttlDays <= 0) return 0;
  let entries: string[];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return 0;
  }
  const cutoff = now.getTime() - ttlDays * 24 * 60 * 60 * 1000;
  let removed = 0;
  for (const name of entries) {
    if (!name.endsWith(".json")) continue;
    const file = path.join(dir, name);
    try {
      if (fs.statSync(file).mtimeMs < cutoff) {
        fs.unlinkSync(file);
        removed++;
      }
    } catch {
      // A file that vanished or refuses stat/unlink is skipped; the
      // sweep is hygiene, not a guarantee.
    }
  }
  return removed;
}
