// Shell log: stdout + an append-only file under userData. Deliberately small —
// no dependency. Every refusal, crash and lifecycle event goes through here
// so "refused and logged" is one line, greppable.
//
// URLs are logged REDACTED (scheme://host/path, query and fragment dropped):
// login redirects and deep links carry tickets in the query string, and the
// standing rule is never to print a token.

import fs from "node:fs";
import path from "node:path";

export type LogLevel = "info" | "warn" | "error";

export interface Logger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
  /** Path of the file sink, once attached. */
  readonly filePath: string | null;
  attachFile(filePath: string): void;
  /** Every line written so far (probe mode reads this for evidence). */
  readonly lines: readonly string[];
}

export function createLogger(): Logger {
  const lines: string[] = [];
  let stream: fs.WriteStream | null = null;
  let filePath: string | null = null;

  function write(level: LogLevel, message: string): void {
    const line = `${new Date().toISOString()} ${level.toUpperCase().padEnd(5)} ${message}`;
    lines.push(line);
    (level === "error" ? process.stderr : process.stdout).write(line + "\n");
    stream?.write(line + "\n");
  }

  return {
    info: (m) => write("info", m),
    warn: (m) => write("warn", m),
    error: (m) => write("error", m),
    get filePath() {
      return filePath;
    },
    get lines() {
      return lines;
    },
    attachFile(target: string) {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      // FIX33 B8 — rotate at open: shell.log → shell.log.1 → shell.log.2,
      // keep LOG_KEEP files. Rotating only here (not mid-write) keeps the
      // sink a plain append stream; an unbounded log under userData was the
      // audit's D9-4.
      rotateLogFiles(target, LOG_KEEP);
      stream = fs.createWriteStream(target, { flags: "a" });
      filePath = target;
      // Replay what was logged before the file existed (config load, lock).
      for (const line of lines) stream.write(line + "\n");
    },
  };
}

/** How many files to keep: the live one plus this many rotated (.1, .2). */
export const LOG_KEEP = 3;

/**
 * shell.log → shell.log.1 → shell.log.2 (…up to keep-1); the oldest is
 * dropped. A missing file is a no-op; any I/O error is swallowed — logging
 * must never prevent the app from starting.
 */
export function rotateLogFiles(target: string, keep: number): void {
  try {
    if (!fs.existsSync(target)) return;
    // An empty live file has nothing worth keeping; don't push out history.
    if (fs.statSync(target).size === 0) return;
    for (let i = keep - 1; i >= 1; i--) {
      const from = i === 1 ? target : `${target}.${i - 1}`;
      const to = `${target}.${i}`;
      if (fs.existsSync(from)) {
        if (i === keep - 1 && fs.existsSync(to)) fs.rmSync(to, { force: true });
        fs.renameSync(from, to);
      }
    }
  } catch {
    /* best-effort */
  }
}

/** scheme://host[:port]/path — query and fragment are never logged. */
export function redactUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const suffix = parsed.search || parsed.hash ? " [query/fragment redacted]" : "";
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return `${parsed.origin}${parsed.pathname}${suffix}`;
    }
    // Custom schemes (deep links) have no origin: keep scheme + host + path.
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}${suffix}`;
  } catch {
    return "[unparseable url]";
  }
}
