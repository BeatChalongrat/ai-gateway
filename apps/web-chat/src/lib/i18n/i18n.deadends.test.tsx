// FIX34.1 Part F (F3) — no page on the web surface may tell the user to go
// and use another app.
//
// ChatGPT / Claude never show a menu entry that opens onto "install our
// desktop app" — a feature the web lacks simply has no page. Ours was worse:
// the app it pointed at is Windows-only, unsigned and pre-release. This test
// source-scans every route and component (CODE lines only; comments may
// quote history) for the phrases that made up those walls, so a new one
// cannot land unnoticed. The dictionary is scanned separately: desktop-only
// UI may legitimately *describe* the desktop app ("runs only while the SRAN
// Desktop app is open" on a card the web never renders), but the wall
// string itself must not exist in any locale.

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { dictionaries } from "@/lib/i18n/dict";

const SRC = path.resolve(__dirname, "..", "..");

const WALL = /available in the (SRAN )?Desktop app|not in the browser|isn't available in the desktop app|only in the desktop app|desktopOnly\.notice/i;

function* walk(dir: string): Generator<string> {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else if (/\.(tsx|ts)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) yield p;
  }
}

function codeLines(file: string): string[] {
  return fs
    .readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => {
      const t = line.trimStart();
      return !t.startsWith("//") && !t.startsWith("{/*") && !t.startsWith("*") && !t.startsWith("/*");
    });
}

describe("F3 — no dead ends pointing at the desktop app", () => {
  it("no route or component renders a 'use the desktop app' wall", () => {
    const offenders: string[] = [];
    for (const dir of ["app", "components", "hooks"]) {
      for (const file of walk(path.join(SRC, dir))) {
        codeLines(file).forEach((line, i) => {
          if (WALL.test(line)) offenders.push(`${path.relative(SRC, file)}:${i + 1}: ${line.trim()}`);
        });
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("neither dictionary carries the wall string", () => {
    for (const locale of ["en", "th"] as const) {
      const dict = dictionaries[locale] as Record<string, string>;
      expect(Object.keys(dict)).not.toContain("desktopOnly.notice");
      for (const [k, v] of Object.entries(dict)) {
        expect(v, `${locale}["${k}"]`).not.toMatch(/available in the SRAN Desktop app|not in the browser/i);
        expect(v, `${locale}["${k}"]`).not.toMatch(/ใช้ได้ในแอป SRAN Desktop/);
      }
    }
  });
});
