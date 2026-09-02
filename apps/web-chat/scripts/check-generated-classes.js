#!/usr/bin/env node
// FIX44 Part B — does every colour class we write actually get generated?
//
// Tailwind 3 drops a utility silently when it cannot build it. The one shape
// this app hits is `<util>-<token>/<NN>` on a colour declared as a bare
// `var(--x)` (no <alpha-value> channel form): 139 such classes shipped for
// months, and every one resolved to "no rule" — transparent backgrounds,
// preflight-grey borders, no focus ring (OPEN_DECISIONS #42).
//
// This check compiles the SAME source with the SAME tailwind.config.js
// through the same engine (postcss + tailwindcss) and compares:
//
//   classes written in src  ∩  shape "<util>-<token>[-sub]/<NN>"
//   minus classes present in the generated CSS
//   minus the allowlist (scripts/generated-classes.allowlist.json, one reason each)
//   = classes that will silently do nothing  → exit 1
//
// Scope is deliberately narrow — only colour tokens from tailwind.config.js,
// only with an opacity modifier — because that is the shape that fails
// silently. Dynamic class names (template strings assembled at runtime) are
// out of scope; they would false-positive. A wider net is a separate decision.
//
// Also verified, cheaply: every `<util>-<token>` class WITHOUT a modifier is
// generated (if a token is renamed in the config, this catches the stragglers).
//
//   node scripts/check-generated-classes.js            # exit 1 on any missing class
//   node scripts/check-generated-classes.js --list     # print the survey only
//
// Exported for the test: buildCss(), classesInSource(), findMissing().
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const CONFIG = path.join(ROOT, "tailwind.config.js");
const ALLOWLIST = path.join(__dirname, "generated-classes.allowlist.json");

function flatten(obj, prefix = []) {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (typeof v === "string") out.push(k === "DEFAULT" ? prefix.join("-") : [...prefix, k].join("-"));
    else out.push(...flatten(v, [...prefix, k]));
  }
  return out;
}

const UTILS = ["bg", "text", "border", "ring", "divide", "outline", "from", "to", "via", "placeholder", "decoration", "shadow", "caret", "fill", "stroke", "ring-offset", "border-t", "border-b", "border-l", "border-r", "border-x", "border-y"];

function tokens(config) {
  return flatten(config.theme.extend.colors).sort((a, b) => b.length - a.length);
}

/** Every `<variant:>*<util>-<token>(/<NN>)?` occurrence in the source, deduplicated. */
function classesInSource(config, files) {
  const toks = tokens(config);
  const re = new RegExp(`(?<![\\w-])((?:[\\w-]+:)*)(${UTILS.map((u) => u.replace(/-/g, "\\-")).join("|")})-(${toks.join("|")})(/\\d{1,3})?(?![\\w-/])`, "g");
  const found = new Map();
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    text.split("\n").forEach((line, i) => {
      for (const m of line.matchAll(re)) {
        const cls = m[0];
        if (!found.has(cls)) found.set(cls, []);
        found.get(cls).push(`${path.relative(ROOT, file).replace(/\\/g, "/")}:${i + 1}`);
      }
    });
  }
  return found;
}

function sourceFiles(dir = SRC, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) sourceFiles(p, acc);
    else if (/\.(tsx?|jsx?)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) acc.push(p);
  }
  return acc;
}

/** Compile `@tailwind utilities` for the given raw content with the app's config. */
async function buildCss(config, rawContent) {
  const postcss = require("postcss");
  const tailwind = require("tailwindcss");
  const r = await postcss([tailwind({ ...config, content: [{ raw: rawContent, extension: "html" }] })]).process("@tailwind utilities;", { from: undefined });
  return r.css;
}

/** CSS selector escape for a Tailwind class name (only the characters we emit). */
function selectorOf(cls) {
  return "." + cls.replace(/[:/.\[\]%]/g, (c) => "\\" + c);
}

/** Classes from `wanted` that produce no rule in `css`. */
function findMissing(wanted, css) {
  const missing = [];
  for (const cls of wanted) {
    const sel = selectorOf(cls);
    // a variant class like hover:bg-x/10 compiles to `.hover\:bg-x\/10:hover`
    if (!css.includes(sel)) missing.push(cls);
  }
  return missing;
}

function loadAllowlist() {
  if (!fs.existsSync(ALLOWLIST)) return new Map();
  const j = JSON.parse(fs.readFileSync(ALLOWLIST, "utf8"));
  const m = new Map();
  for (const e of j.entries) {
    if (!e.class || !e.reason) throw new Error(`allowlist entry without class/reason: ${JSON.stringify(e)}`);
    m.set(e.class, e.reason);
  }
  return m;
}

async function main() {
  const config = require(CONFIG);
  const found = classesInSource(config, sourceFiles());
  const all = [...found.keys()].sort();
  if (process.argv.includes("--list")) {
    for (const c of all) console.log(c.padEnd(40), found.get(c).length, "site(s)");
    return 0;
  }
  const css = await buildCss(config, `<div class="${all.join(" ")}"></div>`);
  const missing = findMissing(all, css);
  const allow = loadAllowlist();
  const unexplained = missing.filter((c) => !allow.has(c));
  const stale = [...allow.keys()].filter((c) => !missing.includes(c));
  console.log(`generated-classes: ${all.length} token classes in src · ${missing.length} not generated · ${missing.length - unexplained.length} allowlisted · ${unexplained.length} unexplained`);
  if (stale.length) console.log(`note: ${stale.length} allowlist entr${stale.length === 1 ? "y is" : "ies are"} no longer needed (class now generated or gone): ${stale.join(", ")}`);
  if (unexplained.length) {
    console.log("\nThese classes are written in src but Tailwind generates NO rule for them (the element gets no colour at all):");
    for (const c of unexplained) console.log(`  ${c}\n    ${found.get(c).slice(0, 5).join("\n    ")}${found.get(c).length > 5 ? `\n    … +${found.get(c).length - 5} more` : ""}`);
    console.log("\nFix the class (drop the /NN on an alpha-carrying token, or use a token that exists), or add it to scripts/generated-classes.allowlist.json with a reason.");
    return 1;
  }
  return 0;
}

module.exports = { buildCss, classesInSource, findMissing, selectorOf, sourceFiles, tokens };

if (require.main === module) {
  main().then((code) => process.exit(code), (e) => { console.error(e); process.exit(2); });
}
