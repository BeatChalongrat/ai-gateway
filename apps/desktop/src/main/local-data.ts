// Local-data hygiene: the wipe command and the conversation-state TTL
// sweep (docs/DATA_RETENTION_PLAN.md item 5).
//
// The desktop keeps plaintext derived state under <userData>:
// conversation-state/*.json (per-conversation structured summaries),
// run-ledger/ (per-turn JSONL, already TTL'd in-store for completed
// runs), and logs/shell.log. All of it is re-derivable from the
// server, but until now none of it could be removed from inside the
// app — only an uninstall cleared it.
//
// Two mechanisms:
//   * wipeLocalData — the explicit "Clear local data" action (tray
//     menu, behind a confirm dialog). Removes conversation-state,
//     run-ledger and logs wholesale. Counts only in the result —
//     never file contents.
//   * sweepConversationStateTTL — deletes conversation-state files
//     whose mtime is older than a per-preference window
//     (localStateTtlDays). Runs at boot; OFF by default (0) like
//     every other deleting mechanism in the program — the user (the
//     owner of this local data) turns it on.
//
// Encryption-at-rest for these files is a separate decision and is
// deliberately NOT taken here.
//
// No 'electron' import: unit-tested in plain Node.

import fs from "node:fs";
import path from "node:path";

export interface WipeResult {
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

/** Remove conversation-state, run-ledger and shell logs under
 *  `userDataDir`. Returns per-kind file counts (never contents). */
export function wipeLocalData(userDataDir: string): WipeResult {
  return {
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
  if (!Number.isFinite(ttlDays) || ttlDays <= 0) return 0;
  const dir = path.join(userDataDir, "conversation-state");
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
