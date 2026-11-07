// PINNING TEST — nothing exposes ipcRenderer, nothing uses @electron/remote
// (E1 §4.3, updated in E3), and the IPC transport is confined to one file.
//
// E1 pinned "the preload never mentions ipcRenderer" and "zero channels" —
// the state E3 is defined to change. This version pins the E3 invariants:
//   * ipcRenderer is imported ONLY in the preload, and is never a value the
//     preload exposes (exposeInMainWorld's argument is a literal whose
//     members are checked in bridge-surface.test.ts);
//   * ipcMain is used ONLY in main/bridge.ts, and every ipcMain.on there
//     is the single call inside guardedOn (bridge-protocol.test.ts pins the
//     senderFrame check inside it);
//   * no @electron/remote anywhere; no runtime dependencies.
//
// LIMITS — what this test cannot see:
//   * A text scan of src/**/*.ts with comments stripped. A dynamic form such
//     as require('electron')['ipc' + 'Renderer'], or code outside src/, or a
//     dependency that bundles @electron/remote, all evade it (E3 still has
//     zero runtime dependencies, checked below).
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const SRC = path.resolve(__dirname, "..", "src");
const PKG = path.resolve(__dirname, "..", "package.json");

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : p.endsWith(".ts") ? [p] : [];
  });
}
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^[ \t]*\/\/.*$/gm, "").replace(/([^:"'])\/\/.*$/gm, "$1");
}

describe("no ipcRenderer exposure, no @electron/remote, IPC confined", () => {
  const files = walk(SRC).map((f) => ({ file: path.relative(SRC, f).replace(/\\/g, "/"), text: stripComments(fs.readFileSync(f, "utf8")) }));

  it("scans a non-trivial source tree", () => {
    expect(files.length).toBeGreaterThanOrEqual(12);
  });

  it("never mentions @electron/remote or enableRemoteModule", () => {
    for (const { file, text } of files) {
      expect(text, file).not.toMatch(/@electron\/remote/);
      expect(text, file).not.toMatch(/enableRemoteModule/);
    }
  });

  it("ipcRenderer appears only in the preload, and only contextBridge + ipcRenderer are imported there", () => {
    for (const { file, text } of files) {
      if (file !== "preload/index.ts") expect(text, file).not.toMatch(/ipcRenderer/);
    }
    const preload = files.find((f) => f.file === "preload/index.ts")!;
    const imports = [...preload.text.matchAll(/import\s*\{([^}]*)\}\s*from\s*["']electron["']/g)].map((m) =>
      m[1]!.split(",").map((s) => s.trim()).filter(Boolean).sort(),
    );
    expect(imports).toEqual([["contextBridge", "ipcRenderer"]]);
  });

  it("no exposeInMainWorld anywhere passes ipcRenderer, and there is exactly one", () => {
    let count = 0;
    for (const { file, text } of files) {
      for (const m of text.matchAll(/exposeInMainWorld\(([\s\S]*?)\);/g)) {
        count += 1;
        expect(m[1], file).not.toMatch(/ipcRenderer/);
      }
    }
    expect(count).toBe(1);
  });

  it("ipcMain is used only in main/bridge.ts, through guardedOn", () => {
    for (const { file, text } of files) {
      if (file !== "main/bridge.ts") expect(text, file).not.toMatch(/\bipcMain\b/);
    }
    const bridge = files.find((f) => f.file === "main/bridge.ts")!;
    expect(bridge.text.match(/ipcMain\.(handle|on|once|handleOnce)\(/g)).toEqual(["ipcMain.on("]);
  });

  it("has no runtime dependencies (nothing to hide a bridge in)", () => {
    const pkg = JSON.parse(fs.readFileSync(PKG, "utf8")) as { dependencies?: Record<string, string> };
    expect(pkg.dependencies ?? {}).toEqual({});
  });
});
