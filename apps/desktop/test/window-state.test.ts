// FIX33 B2/B4/B8 — remembered window bounds are VALIDATED against the
// displays that exist now (an unplugged monitor must not leave the window
// off-screen), always-on-top persists beside them, the log rotates at open
// and keeps 3 files, and the menu template never carries DevTools in release.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { accelerators, buildMenuTemplate, hasDevTools, type MenuActions } from "../src/main/app-menu";
import { LOG_KEEP, rotateLogFiles } from "../src/main/logger";
import { DEFAULT_WINDOW_STATE, MIN_SIZE, sanitizeBounds, WindowStateStore } from "../src/main/window-state";

const primary = { x: 0, y: 0, width: 1920, height: 1080 };
const second = { x: 1920, y: 0, width: 2560, height: 1440 };

describe("sanitizeBounds", () => {
  it("accepts bounds that sit on a current display", () => {
    expect(sanitizeBounds({ x: 100, y: 50, width: 1200, height: 820 }, [primary])).toEqual({ x: 100, y: 50, width: 1200, height: 820 });
  });
  it("rejects bounds on a display that is gone (off-screen window)", () => {
    // Saved while on the second monitor; only the primary is connected now.
    expect(sanitizeBounds({ x: 2000, y: 100, width: 1200, height: 820 }, [primary])).toBeNull();
    // With the second monitor back they are fine.
    expect(sanitizeBounds({ x: 2000, y: 100, width: 1200, height: 820 }, [primary, second])).not.toBeNull();
  });
  it("rejects nonsense (too small, non-finite, null)", () => {
    expect(sanitizeBounds(null, [primary])).toBeNull();
    expect(sanitizeBounds({ x: 0, y: 0, width: MIN_SIZE.width - 1, height: 600 }, [primary])).toBeNull();
    expect(sanitizeBounds({ x: Number.NaN, y: 0, width: 800, height: 600 }, [primary])).toBeNull();
  });
  it("a corner still visible is enough; a sliver is not", () => {
    expect(sanitizeBounds({ x: 1700, y: 900, width: 1200, height: 820 }, [primary])).not.toBeNull(); // 220×180 visible
    expect(sanitizeBounds({ x: 1850, y: 1000, width: 1200, height: 820 }, [primary])).toBeNull(); // 70×80 visible
  });
  it("clamps a 4K-sized window to the laptop screen it comes back on", () => {
    const r = sanitizeBounds({ x: 0, y: 0, width: 2560, height: 1440 }, [primary]);
    expect(r).toEqual({ x: 0, y: 0, width: 1920, height: 1080 });
  });
});

describe("WindowStateStore", () => {
  let dir: string;
  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "sran-ws-"));
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it("round-trips bounds, maximized and alwaysOnTop; a missing/corrupt file yields defaults", () => {
    const p = path.join(dir, "window-state.json");
    const s = new WindowStateStore(p);
    expect(s.load()).toEqual(DEFAULT_WINDOW_STATE);
    s.update({ bounds: { x: 1, y: 2, width: 800, height: 600 }, alwaysOnTop: true }, true);
    s.update({ maximized: true }, true);
    const again = new WindowStateStore(p).load();
    expect(again).toEqual({ bounds: { x: 1, y: 2, width: 800, height: 600 }, maximized: true, alwaysOnTop: true });
    fs.writeFileSync(p, "{not json");
    expect(new WindowStateStore(p).load()).toEqual(DEFAULT_WINDOW_STATE);
  });
});

describe("rotateLogFiles", () => {
  let dir: string;
  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "sran-log-"));
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it("keeps 3 files: live, .1, .2 — the oldest falls off", () => {
    const p = path.join(dir, "shell.log");
    expect(LOG_KEEP).toBe(3);
    for (let run = 1; run <= 4; run++) {
      rotateLogFiles(p, LOG_KEEP);
      fs.writeFileSync(p, `run ${run}\n`);
    }
    expect(fs.readFileSync(p, "utf8")).toBe("run 4\n");
    expect(fs.readFileSync(`${p}.1`, "utf8")).toBe("run 3\n");
    expect(fs.readFileSync(`${p}.2`, "utf8")).toBe("run 2\n");
    expect(fs.existsSync(`${p}.3`)).toBe(false);
  });
  it("a missing or empty live file rotates nothing", () => {
    const p = path.join(dir, "shell.log");
    rotateLogFiles(p, LOG_KEEP);
    expect(fs.existsSync(`${p}.1`)).toBe(false);
    fs.writeFileSync(p, "");
    rotateLogFiles(p, LOG_KEEP);
    expect(fs.existsSync(`${p}.1`)).toBe(false);
  });
});

describe("app menu template", () => {
  const actions: MenuActions = {
    newChat: () => {}, openSettings: () => {}, reload: () => {}, toggleAlwaysOnTop: () => {}, isAlwaysOnTop: () => true, openLogs: () => {}, about: () => {}, quit: () => {}, toggleDevTools: () => {},
  };
  it("carries the accelerators the spec names and never DevTools in release", () => {
    const rel = buildMenuTemplate(actions, { isRelease: true });
    const acc = accelerators(rel);
    expect(acc).toContain("CmdOrCtrl+N");
    expect(acc).toContain("CmdOrCtrl+,");
    expect(acc).toContain("CmdOrCtrl+Shift+T");
    expect(hasDevTools(rel)).toBe(false);
    expect(hasDevTools(buildMenuTemplate(actions, { isRelease: false }))).toBe(true);
    expect(rel.map((m) => m.label)).toEqual(["File", "Edit", "View", "Help"]);
  });
  it("the always-on-top item reflects the live value", () => {
    const view = buildMenuTemplate(actions, { isRelease: true }).find((m) => m.label === "View")!;
    const aot = view.submenu!.find((i) => i.label === "Always on Top")!;
    expect(aot.type).toBe("checkbox");
    expect(aot.checked).toBe(true);
  });
});
