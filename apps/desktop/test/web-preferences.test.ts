// PINNING TEST — webPreferences (E1 §4.1).
//
// Pins: contextIsolation:true, sandbox:true, nodeIntegration:false (plus the
// secondary locks: no webviewTag, no node in workers/subframes, webSecurity
// on) and devTools off when isRelease.
//
// LIMITS — what this test cannot see:
//   * It pins the OUTPUT OF buildWebPreferences(), not what `new
//     BrowserWindow` received. The link between the two is a source-text
//     check below (index.ts must contain `webPreferences: buildWebPreferences(`)
//     which passes if index.ts is renamed/moved (the read would then throw —
//     loudly, at least) and cannot see a second BrowserWindow created
//     elsewhere with hand-written preferences.
//   * It cannot see Electron overriding a flag at runtime (e.g. sandbox being
//     forced off by a command-line switch such as --no-sandbox). The live
//     probe (`npm run probe`, check 6) covers that from the page's side.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { buildWebPreferences } from "../src/main/web-preferences";

const INDEX_TS = path.resolve(__dirname, "..", "src", "main", "index.ts");

describe("webPreferences", () => {
  it("locks the renderer: contextIsolation, sandbox, no nodeIntegration", () => {
    const prefs = buildWebPreferences({ preloadPath: "C:/x/preload.js", isRelease: true });
    expect(prefs.contextIsolation).toBe(true);
    expect(prefs.sandbox).toBe(true);
    expect(prefs.nodeIntegration).toBe(false);
    expect(prefs.nodeIntegrationInWorker).toBe(false);
    expect(prefs.nodeIntegrationInSubFrames).toBe(false);
    expect(prefs.webviewTag).toBe(false);
    expect(prefs.webSecurity).toBe(true);
    expect(prefs.allowRunningInsecureContent).toBe(false);
    expect(Object.isFrozen(prefs)).toBe(true);
  });

  it("has DevTools off in release and on in dev", () => {
    expect(buildWebPreferences({ preloadPath: "p", isRelease: true }).devTools).toBe(false);
    expect(buildWebPreferences({ preloadPath: "p", isRelease: false }).devTools).toBe(true);
  });

  it("passes the preload path through unchanged", () => {
    expect(buildWebPreferences({ preloadPath: "C:/x/preload.js", isRelease: false }).preload).toBe("C:/x/preload.js");
  });

  it("is what the main window is built with (source-text link, see LIMITS)", () => {
    const source = fs.readFileSync(INDEX_TS, "utf8");
    expect(source).toMatch(/webPreferences:\s*buildWebPreferences\(/);
    // E1 built exactly one BrowserWindow; FIX34 Part A adds the quick-ask
    // window. The invariant that matters: EVERY window is built with the
    // locked preferences — the two counts must stay equal.
    const windows = source.match(/new BrowserWindow\(/g)?.length ?? 0;
    const locked = source.match(/webPreferences:\s*buildWebPreferences\(/g)?.length ?? 0;
    expect(windows).toBe(2);
    expect(locked).toBe(windows);
    // isRelease must come from app.isPackaged, not a literal.
    expect(source).toMatch(/const isRelease = app\.isPackaged;/);
  });
});
