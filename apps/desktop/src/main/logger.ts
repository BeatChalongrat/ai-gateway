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
      stream = fs.createWriteStream(target, { flags: "a" });
      filePath = target;
      // Replay what was logged before the file existed (config load, lock).
      for (const line of lines) stream.write(line + "\n");
    },
  };
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
