// FIX33 B2 / B4 — remembered window state: bounds, maximized, always-on-top.
//
// Saved as <userData>/window-state.json when the window moves / resizes /
// closes; read once at creation. The saved bounds are VALIDATED against the
// displays that exist NOW (`screen.getAllDisplays()` in index.ts) — the
// monitor the window was on last time may be unplugged, and an off-screen
// window is the classic "the app opens but I can't see it" bug. When the
// saved rectangle is not sufficiently on any display, the defaults win.
//
// No 'electron' import: the validation is a pure function, unit-tested with
// synthetic display rectangles.

import fs from "node:fs";
import path from "node:path";

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WindowState {
  bounds: Rect | null;
  maximized: boolean;
  alwaysOnTop: boolean;
}

export const DEFAULT_WINDOW_STATE: WindowState = { bounds: null, maximized: false, alwaysOnTop: false };

/** Minimum size the shell allows (createMainWindow's minWidth/minHeight). */
export const MIN_SIZE = { width: 640, height: 480 };
export const DEFAULT_SIZE = { width: 1200, height: 820 };

/** At least this many pixels of the window must sit inside SOME display. */
const MIN_VISIBLE = { width: 200, height: 100 };

/**
 * Returns the saved bounds when they are usable on the current displays,
 * else null (caller uses defaults). "Usable" = a sane size and at least a
 * MIN_VISIBLE × MIN_VISIBLE corner intersecting one display's work area.
 */
export function sanitizeBounds(saved: Rect | null | undefined, displays: readonly Rect[]): Rect | null {
  if (!saved) return null;
  const { x, y, width, height } = saved;
  if (![x, y, width, height].every((n) => Number.isFinite(n))) return null;
  if (width < MIN_SIZE.width || height < MIN_SIZE.height) return null;
  if (width > 20_000 || height > 20_000) return null;
  for (const d of displays) {
    const ix = Math.min(x + width, d.x + d.width) - Math.max(x, d.x);
    const iy = Math.min(y + height, d.y + d.height) - Math.max(y, d.y);
    if (ix >= MIN_VISIBLE.width && iy >= MIN_VISIBLE.height) {
      // Clamp the size to the display so a window saved on a 4K monitor is
      // not larger than the laptop screen it comes back on.
      return { x, y, width: Math.min(width, d.width), height: Math.min(height, d.height) };
    }
  }
  return null;
}

export class WindowStateStore {
  private state: WindowState = { ...DEFAULT_WINDOW_STATE };
  private timer: NodeJS.Timeout | null = null;
  constructor(private readonly filePath: string) {}

  load(): WindowState {
    try {
      if (fs.existsSync(this.filePath)) {
        const parsed = JSON.parse(fs.readFileSync(this.filePath, "utf8")) as Partial<WindowState>;
        this.state = {
          bounds: parsed.bounds && typeof parsed.bounds === "object" ? parsed.bounds : null,
          maximized: parsed.maximized === true,
          alwaysOnTop: parsed.alwaysOnTop === true,
        };
      }
    } catch {
      this.state = { ...DEFAULT_WINDOW_STATE };
    }
    return { ...this.state };
  }

  current(): WindowState {
    return { ...this.state };
  }

  /** Merge + write (debounced: move/resize fire dozens of times a second). */
  update(patch: Partial<WindowState>, immediate = false): void {
    this.state = { ...this.state, ...patch };
    if (immediate) {
      this.flush();
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 400);
  }

  flush(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    try {
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      const tmp = `${this.filePath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(this.state, null, 2));
      fs.renameSync(tmp, this.filePath);
    } catch {
      /* best-effort — a window position is not worth a crash */
    }
  }
}
