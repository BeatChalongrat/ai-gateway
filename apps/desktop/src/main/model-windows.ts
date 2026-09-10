// FIX104 — the last context windows the gateway's catalogue reported.
//
// The window a turn is budgeted against comes from GET /v1/models
// (`context_window`). The turn coordinator already reads that list every
// five minutes for tools/vision; this keeps the windows from the last
// successful read, in memory and in <userData>\model-windows.json, so that:
//
//   · a failed read mid-session keeps the last known windows (the list the
//     coordinator holds does the same);
//   · a cold start while the gateway cannot list budgets against what it
//     said last time, not against a guess;
//   · a cold start with no file at all — a fresh install, or a gateway that
//     has never answered — budgets every model at the conservative default
//     (14,500, chat-edge's defaultContextLimit). That trims early and never
//     overflows; it lasts until the first successful read.
//
// The file is a cache of the catalogue, rewritten whenever the catalogue's
// answer changes. Nobody edits it; deleting it only costs one cold start.

import fs from "node:fs";
import path from "node:path";

import type { Logger } from "./logger";

interface WindowsFile {
  fetched_at: string;
  windows: Record<string, number>;
}

export class ModelWindows {
  private windows = new Map<string, number>();
  private fetchedAt: string | null = null;
  private fromCatalogueThisRun = false;

  constructor(
    private readonly filePath: string | null,
    private readonly log?: Pick<Logger, "info" | "warn">,
  ) {
    if (!filePath) return;
    try {
      const raw = JSON.parse(fs.readFileSync(filePath, "utf8")) as Partial<WindowsFile>;
      for (const [id, w] of Object.entries(raw.windows ?? {})) {
        if (typeof w === "number" && w > 0) this.windows.set(id, Math.floor(w));
      }
      this.fetchedAt = typeof raw.fetched_at === "string" ? raw.fetched_at : null;
      this.log?.info(`CONTEXT WINDOWS: ${this.windows.size} last-known from ${filePath} (catalogue read ${this.fetchedAt ?? "unknown"}) — used until the gateway answers`);
    } catch (err) {
      const missing = (err as NodeJS.ErrnoException).code === "ENOENT";
      this.log?.info(`CONTEXT WINDOWS: ${missing ? "none cached yet" : `cache unreadable (${(err as Error).message.slice(0, 80)})`} — every model budgets at the conservative default until the gateway's catalogue answers`);
    }
  }

  /** A successful catalogue read: id → advertised window (0 = not given). Replaces what was known. */
  learn(entries: Iterable<[string, number]>): void {
    const next = new Map<string, number>();
    for (const [id, w] of entries) {
      if (typeof w === "number" && w > 0) next.set(id, Math.floor(w));
    }
    const changed = next.size !== this.windows.size || [...next].some(([id, w]) => this.windows.get(id) !== w);
    this.windows = next;
    this.fetchedAt = new Date().toISOString();
    if (!this.fromCatalogueThisRun || changed) {
      const sample = ["Typhoon-S-ThaiLLM-8B-Instruct", "qwen3.8-27b-fp8"].filter((id) => next.has(id)).map((id) => `${id}=${next.get(id)}`);
      this.log?.info(`CONTEXT WINDOWS: ${next.size} from the gateway catalogue${sample.length ? ` (${sample.join(", ")})` : ""}${changed ? "" : ", unchanged"}`);
    }
    this.fromCatalogueThisRun = true;
    if (changed) this.persist();
  }

  /** The advertised window the catalogue last reported for this id; 0 when it never did. */
  advertised(modelId: string): number {
    return this.windows.get(modelId) ?? 0;
  }

  private persist(): void {
    if (!this.filePath) return;
    try {
      const body: WindowsFile = { fetched_at: this.fetchedAt ?? new Date().toISOString(), windows: Object.fromEntries(this.windows) };
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      const tmp = `${this.filePath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(body));
      fs.renameSync(tmp, this.filePath);
    } catch (err) {
      this.log?.warn(`CONTEXT WINDOWS: could not write ${this.filePath}: ${(err as Error).message.slice(0, 120)}`);
    }
  }
}
