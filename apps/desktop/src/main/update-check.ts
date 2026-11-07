// FIX22 §2 — update NOTIFICATION, stage 1. Nothing here downloads, installs,
// or names a URL: the host asks the gateway it already talks to for the
// current desktop version (GET /v1/desktop/version — a bare string), compares
// it with its own, and, when the gateway's is newer, posts ONE `host.notice`
// to the page, which shows a small dismissible banner. The user installs by
// hand. Any failure (route missing, error, empty, unparsable) = say nothing;
// an unconfigured deployment looks exactly like today. No retry storm: one
// attempt per check, checks hours apart.
//
// Stage 2 (electron-updater, differential, signed feed) waits for a
// code-signing certificate: the update channel is remote code execution into
// every installed machine. Do not get ahead of that.
//
// Pure where it can be: compareVersions/isNewerVersion have no I/O and are
// tested first; UpdateNotifier takes its collaborators as interfaces.

export interface VersionSource {
  latestDesktopVersion(signal: AbortSignal): Promise<string | null>;
}
export interface NoticeSink {
  post(type: "host.notice", payload: unknown): void;
}
export interface UpdateLogger {
  info(message: string): void;
  warn(message: string): void;
}

/** `1.2.3-e10` → { core: [1,2,3], pre: ["e", 10] } ; null when unparsable. */
export function parseVersion(v: string): { core: number[]; pre: (string | number)[] | null } | null {
  const m = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/.exec(v.trim());
  if (!m) return null;
  const core = [Number(m[1]), Number(m[2] ?? 0), Number(m[3] ?? 0)];
  if (!m[4]) return { core, pre: null };
  // Split a pre-release into alternating alpha/numeric runs so "e10" sorts
  // above "e9" (numeric runs compare as numbers), and "rc.2" above "rc.1".
  const pre: (string | number)[] = [];
  for (const piece of m[4].split(".")) {
    for (const run of piece.match(/\d+|[^\d]+/g) ?? []) pre.push(/^\d+$/.test(run) ? Number(run) : run);
  }
  return { core, pre };
}

/** Semver-style compare: -1 if a<b, 0 if equal, 1 if a>b. A release is newer
 * than its own pre-releases (1.0.0 > 1.0.0-e9); numeric pre-release runs
 * compare numerically (e10 > e9); unparsable input compares as equal so a
 * garbage version can never trigger a banner. */
export function compareVersions(a: string, b: string): -1 | 0 | 1 {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  if (!pa || !pb) return 0;
  for (let i = 0; i < 3; i++) {
    if (pa.core[i]! !== pb.core[i]!) return pa.core[i]! < pb.core[i]! ? -1 : 1;
  }
  if (pa.pre === null && pb.pre === null) return 0;
  if (pa.pre === null) return 1; // release > pre-release
  if (pb.pre === null) return -1;
  const n = Math.max(pa.pre.length, pb.pre.length);
  for (let i = 0; i < n; i++) {
    const x = pa.pre[i];
    const y = pb.pre[i];
    if (x === undefined) return -1; // shorter pre-release list is lower (e9 < e9.1)
    if (y === undefined) return 1;
    if (x === y) continue;
    if (typeof x === "number" && typeof y === "number") return x < y ? -1 : 1;
    if (typeof x === "number") return -1; // numeric < alpha (semver §11)
    if (typeof y === "number") return 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

export function isNewerVersion(latest: string, current: string): boolean {
  return compareVersions(latest, current) === 1;
}

export interface UpdateNotice {
  readonly kind: "update-available";
  readonly latest: string;
  readonly current: string;
}

export class UpdateNotifier {
  private notice: UpdateNotice | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  static readonly INTERVAL_MS = 6 * 60 * 60 * 1000;
  static readonly TIMEOUT_MS = 8_000;

  constructor(
    private readonly source: VersionSource,
    private readonly sink: NoticeSink,
    private readonly currentVersion: string,
    private readonly log: UpdateLogger,
  ) {}

  /** One check. Posts a notice only when the gateway's version is newer.
   * Every failure path returns null and posts nothing. Never throws. */
  async check(): Promise<UpdateNotice | null> {
    if (this.inFlight) return this.notice;
    this.inFlight = true;
    try {
      const latest = (await this.source.latestDesktopVersion(AbortSignal.timeout(UpdateNotifier.TIMEOUT_MS)))?.trim() ?? "";
      if (!latest) {
        this.log.info("UPDATE CHECK: no version published on the gateway — nothing to show");
        return null;
      }
      if (!isNewerVersion(latest, this.currentVersion)) {
        this.log.info(`UPDATE CHECK: gateway has ${latest}, this app is ${this.currentVersion} — up to date`);
        return null;
      }
      this.notice = { kind: "update-available", latest, current: this.currentVersion };
      this.log.info(`UPDATE CHECK: ${latest} is newer than ${this.currentVersion} — notifying the page`);
      this.sink.post("host.notice", this.notice);
      return this.notice;
    } catch (err) {
      this.log.warn(`UPDATE CHECK: failed quietly (${(err as Error).message}) — nothing shown`);
      return null;
    } finally {
      this.inFlight = false;
    }
  }

  /** The page reloaded / navigated: re-post a notice we already have so the
   * banner survives a page reload (the page decides whether it was dismissed). */
  repostIfPending(): void {
    if (this.notice) this.sink.post("host.notice", this.notice);
  }

  private lastAttemptAt = 0;
  static readonly PAGE_READY_MIN_GAP_MS = 10 * 60 * 1000;

  /** ui.ready hook: repost a pending notice; otherwise run a check — but at
   * most once per PAGE_READY_MIN_GAP_MS, so a reload loop cannot become a
   * request loop. The first check after sign-in typically happens here (the
   * key exists once the page is up). */
  onPageReady(): void {
    if (this.notice) {
      this.repostIfPending();
      return;
    }
    const now = Date.now();
    if (now - this.lastAttemptAt < UpdateNotifier.PAGE_READY_MIN_GAP_MS) return;
    this.lastAttemptAt = now;
    void this.check();
  }

  start(): void {
    if (this.timer) return;
    void this.check();
    this.timer = setInterval(() => void this.check(), UpdateNotifier.INTERVAL_MS);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
