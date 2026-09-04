// Local-data hygiene tests: the wipe removes the three cache dirs and
// reports counts only; the TTL sweep is OFF at 0 (the default),
// removes only stale .json files when on, and never touches fresh
// ones. All in temp dirs — nothing near a real profile.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { sweepConversationStateTTL, wipeLocalData } from "../src/main/local-data";
import { DEFAULT_SHELL_PREFS, sanitizePrefs } from "../src/main/shell-prefs";

let tmp: string | null = null;

function mkUserData(): string {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-localdata-"));
  return tmp;
}

afterEach(() => {
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
  tmp = null;
});

function write(dir: string, name: string, mtime?: Date): string {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, "{}");
  if (mtime) fs.utimesSync(file, mtime, mtime);
  return file;
}

describe("wipeLocalData", () => {
  it("removes conversation-state, run-ledger and logs, reporting counts", () => {
    const ud = mkUserData();
    write(path.join(ud, "conversation-state"), "a.json");
    write(path.join(ud, "conversation-state"), "b.json");
    write(path.join(ud, "run-ledger"), "r1.jsonl");
    write(path.join(ud, "logs"), "shell.log");
    // Files the wipe must NOT touch (auth/session live elsewhere).
    write(ud, "session.bin");

    const counts = wipeLocalData(ud);
    expect(counts).toEqual({
      conversationStateFiles: 2,
      runLedgerFiles: 1,
      logFiles: 1,
    });
    expect(fs.existsSync(path.join(ud, "conversation-state"))).toBe(false);
    expect(fs.existsSync(path.join(ud, "run-ledger"))).toBe(false);
    expect(fs.existsSync(path.join(ud, "logs"))).toBe(false);
    expect(fs.existsSync(path.join(ud, "session.bin"))).toBe(true);
  });

  it("is a clean no-op on an empty profile", () => {
    const ud = mkUserData();
    expect(wipeLocalData(ud)).toEqual({
      conversationStateFiles: 0,
      runLedgerFiles: 0,
      logFiles: 0,
    });
  });
});

describe("sweepConversationStateTTL", () => {
  it("ttl 0 disables the sweep and touches nothing", () => {
    const ud = mkUserData();
    const old = new Date(Date.now() - 90 * 24 * 3600 * 1000);
    write(path.join(ud, "conversation-state"), "old.json", old);

    // 0 is still the explicit "never sweep" value — a user who wants
    // the cache kept forever sets it. It is no longer the DEFAULT.
    expect(sweepConversationStateTTL(ud, 0)).toBe(0);
    expect(fs.existsSync(path.join(ud, "conversation-state", "old.json"))).toBe(true);
  });

  it("defaults to a 30-day window, so an untouched install forgets", () => {
    // FIX60 — this shipped at 0 (off) on the rule that deleting
    // mechanisms start disabled. On a server that rule is right; here
    // "off" means plaintext transcripts accumulate on a laptop
    // forever, and the cache is only a convenience — the conversation
    // itself lives on the server.
    expect(DEFAULT_SHELL_PREFS.localStateTtlDays).toBe(30);

    const ud = mkUserData();
    const stale = new Date(Date.now() - 45 * 24 * 3600 * 1000);
    const fresh = new Date(Date.now() - 3 * 24 * 3600 * 1000);
    write(path.join(ud, "conversation-state"), "stale.json", stale);
    write(path.join(ud, "conversation-state"), "fresh.json", fresh);

    expect(sweepConversationStateTTL(ud, DEFAULT_SHELL_PREFS.localStateTtlDays)).toBe(1);
    expect(fs.existsSync(path.join(ud, "conversation-state", "stale.json"))).toBe(false);
    expect(fs.existsSync(path.join(ud, "conversation-state", "fresh.json"))).toBe(true);
  });

  it("removes only stale .json files when enabled", () => {
    const ud = mkUserData();
    const dir = path.join(ud, "conversation-state");
    const old = new Date(Date.now() - 90 * 24 * 3600 * 1000);
    write(dir, "old.json", old);
    write(dir, "fresh.json");
    write(dir, "not-json.txt", old);

    expect(sweepConversationStateTTL(ud, 30)).toBe(1);
    expect(fs.existsSync(path.join(dir, "old.json"))).toBe(false);
    expect(fs.existsSync(path.join(dir, "fresh.json"))).toBe(true);
    expect(fs.existsSync(path.join(dir, "not-json.txt"))).toBe(true);
  });
});

describe("shell-prefs localStateTtlDays", () => {
  it("falls back to the 30-day default and rejects invalid values", () => {
    // Garbage falls back to the DEFAULT, not to 0. Falling back to 0
    // would let a corrupted prefs file silently switch the sweep off
    // and keep transcripts forever — the failure mode this default
    // exists to prevent.
    expect(sanitizePrefs(null).localStateTtlDays).toBe(30);
    expect(sanitizePrefs({ localStateTtlDays: -5 } as never).localStateTtlDays).toBe(30);
    expect(sanitizePrefs({ localStateTtlDays: Number.NaN } as never).localStateTtlDays).toBe(30);
    // A deliberate 0 is honoured — it is a choice, not corruption.
    expect(sanitizePrefs({ localStateTtlDays: 0 } as never).localStateTtlDays).toBe(0);
    expect(sanitizePrefs({ localStateTtlDays: 45.9 } as never).localStateTtlDays).toBe(45);
  });
});

describe("shell-prefs BOM tolerance", () => {
  it("reads a BOM'd prefs file (Notepad) without resetting every field", async () => {
    const fsN = await import("node:fs");
    const osN = await import("node:os");
    const pathN = await import("node:path");
    const { ShellPrefsStore } = await import("../src/main/shell-prefs");
    const dir = fsN.mkdtempSync(pathN.join(osN.tmpdir(), "sran-prefs-bom-"));
    try {
      const file = pathN.join(dir, "shell-prefs.json");
      fsN.writeFileSync(file, "﻿" + JSON.stringify({ localStateTtlDays: 30 }));
      expect(new ShellPrefsStore(file).load().localStateTtlDays).toBe(30);
    } finally {
      fsN.rmSync(dir, { recursive: true, force: true });
    }
  });
});
