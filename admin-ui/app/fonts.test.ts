// The admin console's build needs no internet.
//
// Until 14 Oct 2026 eight pages and the layout each called next/font/google
// for Outfit, and `next build` fetched the woff2 from fonts.gstatic.com inside
// the image build; the fetch failed in two builds of three on the day the
// admin image was last built. web-chat vendored its fonts for the same reason
// (apps/web-chat/src/app/layout.tsx says why). The same bytes now live beside
// one loader, app/fonts.ts, and the Dockerfile's build step runs with no
// network at all — so a build that fetched anything would fail there, not
// pass by luck.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const APP = __dirname;
const ADMIN = join(APP, "..");

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

describe("the admin console's fonts are vendored", () => {
  it("no page or layout fetches a font from Google at build time", () => {
    const fetching = sources(APP)
      .filter((p) => /from\s+["']next\/font\/google["']/.test(readFileSync(p, "utf-8")))
      .map((p) => relative(ADMIN, p).replace(/\\/g, "/"));
    expect(fetching, "files that import next/font/google").toEqual([]);
  });

  it("one loader serves Outfit from a file beside it, with its licence", () => {
    const loader = readFileSync(join(APP, "fonts.ts"), "utf-8");
    expect(loader).toMatch(/from\s+["']next\/font\/local["']/);
    const m = loader.match(/path:\s*["']\.\/fonts\/([^"']+\.woff2)["']/);
    expect(m, "the loader names a woff2 under app/fonts/").not.toBeNull();
    const file = join(APP, "fonts", m![1]);
    expect(existsSync(file), `${m![1]} is beside the loader`).toBe(true);
    expect(statSync(file).size).toBeGreaterThan(10_000);
    // the OFL requires its notice to travel with the font
    expect(readFileSync(join(APP, "fonts", "OFL-Outfit.txt"), "utf-8")).toMatch(/SIL OPEN FONT LICENSE/i);
    expect(loader).toMatch(/variable:\s*["']--font-outfit["']/); // globals.css reads this name
  });

  it("the image's build step has no network, so nothing can be fetched by accident", () => {
    const dockerfile = readFileSync(join(ADMIN, "Dockerfile"), "utf-8");
    expect(dockerfile).toMatch(/^RUN --network=none npm run build$/m);
  });
});
