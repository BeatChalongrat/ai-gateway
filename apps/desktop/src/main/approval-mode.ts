// FIX25 §1b/§1c — the approval mode as a LIVE setting.
//
// Before this round the mode was captured ONCE, from the Electron config file,
// when the coordinator was constructed (index.ts), while the sidecar kept its
// own `approval_mode` (read by its run_command blocklist bypass). Two stores,
// one frozen. Now the sidecar's settings file is the store of record: the
// composer writes it through the existing `settings.set` op (page-only —
// never a tool), and the host READS it per tool call through `settings.get`,
// so a change applies to the next tool call, never retroactively (§1c.4).
// The config value survives only as the fallback when the sidecar cannot
// answer.
//
// §1c.3 — `skip` is not sticky: on app start a stored `skip` is written back
// as `manual` and the page is told once (a `host.notice`). No electron import:
// unit-tested in plain Node.

import type { ApprovalMode } from "./permissions";

/** Strict: only the three real values, else null (no "unknown → auto"
 * laundering here — that fallback belongs to parseApprovalMode alone). */
export function parseStoredApprovalMode(raw: unknown): ApprovalMode | null {
  if (typeof raw !== "string") return null;
  const v = raw.trim().toLowerCase();
  return v === "manual" || v === "auto" || v === "skip" ? v : null;
}

/** The mode a stored value comes back as at app start (§1c.3). */
export function startupApprovalMode(stored: ApprovalMode | null): { mode: ApprovalMode | null; reset: boolean } {
  return stored === "skip" ? { mode: "manual", reset: true } : { mode: stored, reset: false };
}

export interface ModeStore {
  hostQuery(op: string, args?: Record<string, unknown>): Promise<{ ok: boolean; data?: unknown; error?: string }>;
}
export interface ModeNoticeSink {
  post(type: "host.notice", payload: unknown): void;
}
export interface ModeLogger {
  info(message: string): void;
  warn(message: string): void;
}

export interface ApprovalModeResetNotice {
  kind: "approval-mode-reset";
  from: ApprovalMode;
  to: ApprovalMode;
}

/** Reads the mode from the sidecar (the store of record); on failure the
 * fallback (the config value) — and says so once per change of outcome. */
export class LiveApprovalMode {
  private lastLogged: string | null = null;
  constructor(
    private readonly store: ModeStore,
    private readonly fallback: ApprovalMode,
    private readonly log: ModeLogger,
  ) {}

  async current(): Promise<ApprovalMode> {
    let mode: ApprovalMode | null = null;
    let source = "sidecar";
    try {
      const r = await this.store.hostQuery("settings.get", {});
      mode = r.ok ? parseStoredApprovalMode((r.data as { approvalMode?: unknown } | undefined)?.approvalMode) : null;
      if (mode === null) source = r.ok ? "sidecar-no-value" : `sidecar-error:${r.error ?? "?"}`;
    } catch (err) {
      source = `sidecar-threw:${(err as Error).message}`;
    }
    const effective = mode ?? this.fallback;
    const stamp = `${effective}/${source}`;
    if (stamp !== this.lastLogged) {
      this.lastLogged = stamp;
      this.log.info(`MODE approvalMode=${effective} (${mode === null ? `fallback config; ${source}` : source})`);
    }
    return effective;
  }
}

/** §1c.3 — the startup reset of a stored `skip`, plus the one-time notice. */
export class ApprovalModeResetter {
  private pending: ApprovalModeResetNotice | null = null;
  private pageReady = false;
  constructor(
    private readonly store: ModeStore,
    private readonly sink: ModeNoticeSink,
    private readonly log: ModeLogger,
  ) {}

  /** Run once after the sidecar is up. Returns true when a reset was written. */
  async resetIfSkip(): Promise<boolean> {
    let stored: ApprovalMode | null = null;
    try {
      const r = await this.store.hostQuery("settings.get", {});
      stored = r.ok ? parseStoredApprovalMode((r.data as { approvalMode?: unknown } | undefined)?.approvalMode) : null;
    } catch (err) {
      this.log.warn(`approval-mode startup read failed: ${(err as Error).message}`);
      return false;
    }
    const decision = startupApprovalMode(stored);
    if (!decision.reset || decision.mode === null) return false;
    const w = await this.store.hostQuery("settings.set", { key: "approvalMode", value: decision.mode }).catch((err: Error) => ({ ok: false, error: err.message }));
    if (!w.ok) {
      this.log.warn(`approval-mode startup reset FAILED (skip stays stored): ${w.error ?? "?"}`);
      return false;
    }
    this.log.info(`MODE startup: stored 'skip' reset to '${decision.mode}' (skip never persists across restarts)`);
    this.pending = { kind: "approval-mode-reset", from: "skip", to: decision.mode };
    if (this.pageReady) this.deliver();
    return true;
  }

  /** ui.ready hook. Delivers a pending notice once; later ui.ready calls
   * (reloads) do not repeat it — the user is told once. */
  onPageReady(): void {
    this.pageReady = true;
    if (this.pending) this.deliver();
  }

  private deliver(): void {
    if (!this.pending) return;
    this.sink.post("host.notice", this.pending);
    this.pending = null;
  }
}
