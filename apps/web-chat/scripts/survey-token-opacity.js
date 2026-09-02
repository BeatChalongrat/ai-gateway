#!/usr/bin/env node
// FIX44 Part A — where does the app write `<utility>-<token>/<NN>`?
//
// Every colour in tailwind.config.js is a bare `var(--x)`. Tailwind 3 cannot
// apply an opacity modifier to such a colour, so `bg-status-danger/10` is not
// generated at all — no warning, no error; the element simply has no rule of
// its own. This script lists every such class in src, grouped the way the
// fixes differ:
//
//   A  border-*/NN  the border tokens already carry alpha; the modifier was
//                   never meaningful. Dropping /NN restores the intended line.
//   B  bg/text/ring/divide/outline on an OPAQUE token (status-*, accent,
//                   text-*) — the tint was real intent; dropping /NN would
//                   paint it solid. Needs a token or alpha-capable colours.
//   C  bg-bg-*/NN   backdrops and sheets — may be a fully transparent overlay.
//
//   node scripts/survey-token-opacity.js [--json]
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const cfg = require(path.join(ROOT, "tailwind.config.js"));

// Flatten the colour tokens: bg.base → "bg-base", border.DEFAULT → "border".
function flatten(obj, prefix = []) {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (typeof v === "string") out.push(k === "DEFAULT" ? prefix.join("-") : [...prefix, k].join("-"));
    else out.push(...flatten(v, [...prefix, k]));
  }
  return out;
}
const TOKENS = flatten(cfg.theme.extend.colors).sort((a, b) => b.length - a.length);
const UTILS = ["bg", "text", "border", "ring", "divide", "outline", "from", "to", "via", "placeholder", "decoration", "shadow", "accent", "caret", "fill", "stroke", "ring-offset", "border-t", "border-b", "border-l", "border-r", "border-x", "border-y"];
// <variant:>*<utility>-<token>/<NN>  — variants (hover:, dark:, md:) are kept in the match.
const RE = new RegExp(`(?<![\\w-])((?:[\\w-]+:)*)(${UTILS.map((u) => u.replace(/-/g, "\\-")).join("|")})-(${TOKENS.join("|")})/(\\d{1,3})(?![\\w-])`, "g");

function walk(dir, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, acc);
    else if (/\.(tsx?|jsx?|css)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) acc.push(p);
  }
  return acc;
}

// Group by what the TOKEN is, not by the utility: the modifier's meaning
// depends on whether the colour behind it already carries alpha.
const ALPHA_TOKENS = new Set(["border", "border-subtle", "border-strong", "accent-subtle"]);
function groupOf(util, token) {
  if (ALPHA_TOKENS.has(token)) return "A"; // token already translucent → /NN was never meaningful
  if (token.startsWith("bg-")) return "C"; // opaque surface used as a sheet / backdrop
  return "B"; // opaque status / accent / text token → the tint was the intent
}

const rows = [];
for (const file of walk(SRC)) {
  const text = fs.readFileSync(file, "utf8");
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const m of line.matchAll(RE)) {
      rows.push({ file: path.relative(ROOT, file).replace(/\\/g, "/"), line: i + 1, cls: m[0], util: m[2], token: m[3], alpha: Number(m[4]), group: groupOf(m[2], m[3]) });
    }
  });
}

if (process.argv.includes("--json")) {
  process.stdout.write(JSON.stringify(rows, null, 2));
} else {
  const by = (k) => rows.reduce((a, r) => ((a[r[k]] = (a[r[k]] || 0) + 1), a), {});
  console.log(`${rows.length} occurrences · ${new Set(rows.map((r) => r.cls.replace(/^(?:[\w-]+:)*/, ""))).size} distinct classes · ${new Set(rows.map((r) => r.file)).size} files`);
  console.log("by group:", by("group"));
  const byCls = by("cls");
  for (const [c, n] of Object.entries(byCls).sort((a, b) => b[1] - a[1])) console.log(String(n).padStart(4), c);
}
