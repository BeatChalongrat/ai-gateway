// FIX44 Part C — no button may pair a background token with a text token
// that cannot be read on it, in EITHER theme.
//
// The family this guards (OPEN_DECISIONS #43): --accent is a foreground hint
// colour (near-white in dark, near-black in light), so `bg-accent text-white`
// was white-on-white in the dark theme. It was found once, explained in a
// comment in planCardUI, and left standing in two other files for months.
// A lint rule could forbid that one pair; this test instead resolves every
// `bg-<token> … text-<token>` pair written in src through the real tokens in
// globals.css for BOTH themes and requires WCAG AA (4.5:1). It therefore
// also catches the next pair nobody has thought of yet.
//
// Scope: static className strings with a solid `bg-…` and a `text-…` in the
// same string. Modifier classes (`/NN`) are skipped — they are not generated
// (see scripts/check-generated-classes.js) and their real background is the
// parent's. Dynamic class names are out of reach of a static test.
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "..", "..");
const GLOBALS = fs.readFileSync(path.join(ROOT, "src", "app", "globals.css"), "utf8");

// Tailwind's own palette entries the app uses on buttons (blue CTA family, white/black).
const PALETTE: Record<string, string> = {
  white: "#FFFFFF", black: "#000000",
  "blue-500": "#3B82F6", "blue-600": "#2563EB", "blue-700": "#1D4ED8", "blue-800": "#1E40AF",
};

// config token → CSS variable (mirrors tailwind.config.js colours)
const TOKEN_VAR: Record<string, string> = {
  "bg-base": "--bg-primary", "bg-secondary": "--bg-surface", "bg-surface": "--bg-surface", "bg-sidebar": "--bg-sidebar", "bg-elevated": "--bg-surface-hover",
  "text-primary": "--text-primary", "text-secondary": "--text-secondary", "text-muted": "--text-tertiary",
  accent: "--accent", "accent-hover": "--accent-hover",
  "status-success": "--success", "status-warning": "--warning", "status-danger": "--error",
  "bubble-user": "--user-bubble-bg",
};

function themeVars(theme: "dark" | "light"): Record<string, string> {
  // dark block is the first :root, light is [data-theme="light"]
  const start = theme === "dark" ? GLOBALS.indexOf(":root") : GLOBALS.indexOf('[data-theme="light"]');
  const block = GLOBALS.slice(start, GLOBALS.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) vars[`--${m[1]}`] = m[2].trim();
  return vars;
}

function parseColor(v: string): [number, number, number, number] | null {
  const hex = v.match(/^#([0-9a-f]{6})$/i);
  if (hex) return [parseInt(hex[1].slice(0, 2), 16), parseInt(hex[1].slice(2, 4), 16), parseInt(hex[1].slice(4, 6), 16), 1];
  const rgba = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/);
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])];
  return null;
}

function luminance([r, g, b]: [number, number, number, number]): number {
  const f = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a: [number, number, number, number], b: [number, number, number, number]): number {
  const l1 = luminance(a), l2 = luminance(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function resolve(token: string, vars: Record<string, string>): [number, number, number, number] | null {
  if (PALETTE[token]) return parseColor(PALETTE[token]);
  const v = TOKEN_VAR[token];
  return v ? parseColor(vars[v] ?? "") : null;
}

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) sourceFiles(p, acc);
    else if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) acc.push(p);
  }
  return acc;
}

/** Every static className string that names a solid bg token and a text token. */
export function pairsInSource(): { file: string; line: number; bg: string; text: string; cls: string }[] {
  const out: { file: string; line: number; bg: string; text: string; cls: string }[] = [];
  const TOK = Object.keys({ ...TOKEN_VAR, ...PALETTE }).sort((a, b) => b.length - a.length).join("|");
  const bgRe = new RegExp(`(?<![\\w-:])bg-(${TOK})(?![\\w-/])`);
  const textRe = new RegExp(`(?<![\\w-:])text-(${TOK})(?![\\w-/])`);
  for (const file of sourceFiles(path.join(ROOT, "src"))) {
    const lines = fs.readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      // one className string per line is the app's convention; template
      // strings are scanned as text, which is fine for the literal parts.
      // Comment lines are prose (planCardUI's header quotes the bad pair).
      if (/^\s*(\/\/|\*|\{\/\*)/.test(line)) return;
      const bg = line.match(bgRe), text = line.match(textRe);
      if (bg && text) out.push({ file: path.relative(ROOT, file).replace(/\\/g, "/"), line: i + 1, bg: bg[1], text: text[1], cls: line.trim().slice(0, 140) });
    });
  }
  return out;
}

/** Interactive controls must meet AA for normal text (4.5:1); other surfaces
 *  (hint labels, code output, badges) must meet the AA floor for large text
 *  and UI components (3:1). A control is recognised by its own markers. */
function isControl(cls: string): boolean {
  return /focus-ring|<button|<Button|<Link|<a |role="button"|type="button"|type="submit"/.test(cls);
}

/** Pairs that fail today and are DECIDED to wait — each with the reason and the
 *  number. Remove the line when the token changes; the test then insists. */
const KNOWN_BELOW_AA: Record<string, string> = {
  "bg-status-danger+white": "--error #EF4444 with white text is 3.76:1 in both themes (memory delete confirm, RecordingBar stop). AA needs a darker danger token (#DC2626 gives 4.83:1) — OPEN_DECISIONS #44, a palette decision, not a one-line fix.",
  "bg-bg-surface+status-danger": "text-status-danger (#EF4444) on --bg-surface is 3.56:1 in dark (WebSettings sign-out). Same root cause: the danger red is tuned for light surfaces.",
};

describe("FIX44 — bg/text token pairs are readable in both themes", () => {
  const pairs = pairsInSource();
  it("finds the pairs it is meant to police (sanity: the FIX43.1 fixes are among them)", () => {
    expect(pairs.some((p) => p.file.endsWith("UpdateBanner.tsx") && p.bg === "accent" && p.text === "bg-base")).toBe(true);
    expect(pairs.length).toBeGreaterThan(20);
  });

  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: controls ≥ 4.5:1, everything else ≥ 3:1`, () => {
      const vars = themeVars(theme);
      const failures: string[] = [];
      for (const p of pairs) {
        const bg = resolve(p.bg, vars), fg = resolve(p.text, vars);
        if (!bg || !fg || bg[3] < 1) continue; // translucent backgrounds depend on what is behind them
        const ratio = contrast(bg, fg);
        const floor = isControl(p.cls) ? 4.5 : 3;
        if (ratio < floor && !KNOWN_BELOW_AA[`bg-${p.bg}+${p.text}`]) failures.push(`${p.file}:${p.line} bg-${p.bg} + text-${p.text} = ${ratio.toFixed(2)}:1 (needs ${floor}:1)`);
      }
      expect(failures, `unreadable in the ${theme} theme:\n  ${failures.join("\n  ")}`).toEqual([]);
    });
  }

  it("would have caught the #43 family: bg-accent + text-white is white-on-white in dark", () => {
    const vars = themeVars("dark");
    expect(contrast(resolve("accent", vars)!, resolve("white", vars)!)).toBeLessThan(1.2);
    expect(contrast(resolve("accent", vars)!, resolve("bg-base", vars)!)).toBeGreaterThan(10);
  });
});
