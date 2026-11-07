// FIX87 Part C — the admin and portal design tokens are readable, in both
// themes, on the surfaces they sit on.
//
// contrast.test.ts (FIX44/45/75) resolves web-chat's own bg/text pairs.
// The admin and portal consoles do not compose Tailwind pairs — they read
// tokens straight from their globals.css (`color: var(--text-muted)` on a
// `.sran-table th`, inline `color: "var(--accent-text)"` in the portal), so
// the rule to pin is at the TOKEN level: every text token that small text
// can be set in must reach WCAG AA (4.5:1) against every solid surface
// token, in both theme blocks; the button fill must carry white text at
// 4.5:1 and stand 3:1 (1.4.11) against the canvas. FIX83 row 37 measured
// the headers at 2.66–2.98:1 and the placeholders at 2.5:1 because
// `--text-muted` was tuned for decoration, not for 10–11 px text.
//
// Cross-tree, in the FIX76/FIX86 pattern: this file reads the other trees,
// so a token retune in admin-ui or the portal that slips under AA fails a
// web-chat test — the one lane every push runs.
//
// The two exceptions FIX83 recorded (web-chat's status-danger-solid 2.77:1
// on the dark surface, the Switch on-track #047857 2.44:1) live in
// contrast.test.ts's KNOWN maps and are untouched by this file.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { contrast } from "./contrast.test";

const ROOT = resolve(__dirname, "../../../..");
const read = (p: string) => readFileSync(resolve(ROOT, p), "utf8");

type RGBA = [number, number, number, number];
function hex(v: string): RGBA | null {
  const m = v.trim().match(/^#([0-9a-f]{6})$/i);
  return m ? [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16), 1] : null;
}

/** The `--name: value;` declarations of the block that starts at `selector`. */
function block(css: string, selector: RegExp): Record<string, string> {
  const start = css.search(selector);
  if (start < 0) throw new Error(`no block ${selector}`);
  const body = css.slice(css.indexOf("{", start), css.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) vars[`--${m[1]}`] = m[2].replace(/\/\*.*?\*\//g, "").trim();
  return vars;
}

const TEXT_TOKENS = ["--text-secondary", "--text-tertiary", "--text-muted"];
const SURFACES = ["--bg-base", "--bg-surface", "--bg-surface-2"];

function checkTree(name: string, css: string, light: RegExp, dark: RegExp) {
  const themes = { light: block(css, light), dark: block(css, dark) };
  const failures: string[] = [];
  for (const [theme, vars] of Object.entries(themes)) {
    for (const t of TEXT_TOKENS) {
      const fg = hex(vars[t] ?? "");
      if (!fg) { failures.push(`${name} ${theme}: ${t} is not a hex colour (${vars[t]})`); continue; }
      for (const s of SURFACES) {
        const bg = hex(vars[s] ?? "");
        if (!bg) { failures.push(`${name} ${theme}: ${s} is not a hex colour (${vars[s]})`); continue; }
        const r = contrast(fg, bg);
        if (r < 4.5) failures.push(`${name} ${theme}: ${t} ${vars[t]} on ${s} ${vars[s]} = ${r.toFixed(2)}:1 (AA needs 4.5)`);
      }
    }
  }
  return { themes, failures };
}

describe("FIX87 — admin and portal text tokens meet AA on their surfaces, both themes", () => {
  it("admin-ui: --text-secondary / --text-tertiary / --text-muted", () => {
    const { failures } = checkTree("admin", read("admin-ui/app/globals.css"), /^:root,\s*\n?\s*\[data-theme="light"\]\s*\{/m, /^\[data-theme="dark"\]\s*\{/m);
    expect(failures, "\n" + failures.join("\n")).toEqual([]);
  });

  it("portal: --text-secondary / --text-tertiary / --text-muted", () => {
    const { failures } = checkTree("portal", read("portal/app/globals.css"), /^:root,\s*\[data-theme="light"\]\s*\{/m, /^\[data-theme="dark"\]\s*\{/m);
    expect(failures, "\n" + failures.join("\n")).toEqual([]);
  });

  it("portal: the accent fill carries white text (4.5:1) and stands against the canvas (3:1); accent text reads on the canvas (4.5:1)", () => {
    const css = read("portal/app/globals.css");
    const white: RGBA = [255, 255, 255, 1];
    for (const [theme, sel] of [["light", /^:root,\s*\[data-theme="light"\]\s*\{/m], ["dark", /^\[data-theme="dark"\]\s*\{/m]] as const) {
      const vars = block(css, sel);
      const fill = hex(vars["--accent-primary"])!, text = hex(vars["--accent-text"])!, base = hex(vars["--bg-base"])!, surface = hex(vars["--bg-surface"])!;
      expect(contrast(fill, white), `${theme}: white on --accent-primary`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(fill, base), `${theme}: --accent-primary against the canvas (1.4.11)`).toBeGreaterThanOrEqual(3);
      expect(contrast(text, base), `${theme}: --accent-text on the canvas`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(text, surface), `${theme}: --accent-text on a card`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("portal: accent-coloured text uses the text token, never the fill token", () => {
    const { execSync } = require("node:child_process") as typeof import("node:child_process");
    let out = "";
    try {
      out = execSync('git grep -n "color: \\"var(--accent-primary)\\"" -- "portal/app" "portal/components"', { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    } catch {
      out = ""; // git grep exits 1 when nothing matches — that is the pass
    }
    expect(out, "these paint text with the fill token:\n" + out).toBe("");
  });

  it("landing: the inactive language segment (--ink-3) reads on its track (--grey)", () => {
    const css = read("portal/app/(marketing)/marketing.css");
    // the marketing tokens live under `.marketing-root {`, not `:root`
    const vars = block(css, /^\.marketing-root\s*\{/m);
    const r = contrast(hex(vars["--ink-3"])!, hex(vars["--grey"])!);
    expect(r, `--ink-3 ${vars["--ink-3"]} on --grey ${vars["--grey"]}`).toBeGreaterThanOrEqual(4.5);
  });

  it("the small-text rules still read the tokens (a one-off hex on a header or placeholder would escape the token rule)", () => {
    const admin = read("admin-ui/app/globals.css");
    const th = admin.slice(admin.indexOf(".sran-table th {"), admin.indexOf("}", admin.indexOf(".sran-table th {")));
    expect(th).toMatch(/color:\s*var\(--color-text-muted\)/);
    const ph = admin.slice(admin.indexOf(".sran-input::placeholder {"), admin.indexOf("}", admin.indexOf(".sran-input::placeholder {")));
    expect(ph).toMatch(/color:\s*var\(--color-text-muted\)/);
    for (const page of ["portal/app/(app)/dashboard/page.tsx", "portal/app/(app)/billing/page.tsx"]) {
      const src = read(page);
      const thFn = src.slice(src.indexOf("function Th("), src.indexOf("function Th(") + 700);
      expect(thFn, `${page} Th() should colour with a token`).toMatch(/color:\s*"var\(--text-(muted|tertiary|secondary)\)"/);
    }
  });
});
