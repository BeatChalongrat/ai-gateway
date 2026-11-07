#!/usr/bin/env node
// FIX97 Part B — does every theme utility we write name a token that EXISTS?
//
// check-generated-classes.js asks the opposite question: "a token is
// declared — was a utility generated for it?" That net faces the wrong way.
// The bug in this repo is a utility naming a token that was never declared,
// and Tailwind drops it silently: no error, no warning, no red build. The
// element then inherits whatever colour its nearest coloured ancestor sets —
// usually text-text-primary, the strongest tone in the scale — so the screen
// looks plausible and nobody looks again.
//
// The trap is set by the config itself: the Tailwind key names and the CSS
// variable names disagree, so a developer reading globals.css and writing the
// name they see there gets a dead class. Verified against tailwind.config.js:
//
//   works                | CSS variable behind it   | dead name you would write
//   text-text-muted      | --text-tertiary-rgb      | text-text-tertiary
//   bg-bg-base           | --bg-primary-rgb         | bg-bg-primary
//   bg-bg-elevated       | --bg-surface-hover-rgb   | bg-bg-surface-hover
//   border-border        | --border-default         | border-border-default
//   border-border-strong | --border-hover           | border-border-hover
//   text-status-danger   | --error-rgb              | text-status-error
//
// FIX96 found text-text-tertiary by hand and reported 148 uses in 46 files.
// That is one door of six, which is why this reads the config rather than a
// hard-coded list: add a scale to the theme and it is policed automatically.
//
//   node scripts/check-theme-classes.js           # exit 1 on any dead class
//   node scripts/check-theme-classes.js --list    # print the inventory only
//
// Blind spots, stated rather than discovered later: only STRING literals are
// read — className="…", className={`…`} (the literal parts) and string
// arguments to clsx()/cn(). A class assembled at runtime
// (`text-${tone}-500`) is invisible here, the same blind spot the i18n
// ratchet has and for the same reason. Scope is web-chat's src/ only; the
// portal and admin-ui trees have their own configs and are NOT scanned.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const CONFIG = path.join(ROOT, "tailwind.config.js");
const ALLOWLIST = path.join(__dirname, "theme-classes.allowlist.json");

/** Every colour scale the theme declares: scale -> Set of valid keys. */
function declaredScales() {
  const colors = require(CONFIG).theme.extend.colors;
  const out = new Map();
  for (const [scale, val] of Object.entries(colors)) {
    if (typeof val !== "object" || val === null) continue;
    out.set(scale, new Set(Object.keys(val)));
  }
  return out;
}

/** The utility prefixes that take a colour scale. */
const PREFIXES = ["text", "bg", "border", "ring", "fill", "stroke", "from", "via", "to", "divide", "outline", "shadow", "decoration", "caret", "accent"];

function sourceFiles(dir, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name === ".next") continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) sourceFiles(p, acc);
    else if (/\.(tsx?|jsx?)$/.test(e.name)) acc.push(p);
  }
  return acc;
}

/**
 * Class-name-bearing string literals. Deliberately conservative: a literal
 * that never contains a theme scale name costs nothing to skip, and a
 * runtime-assembled name is not visible to any static reader.
 */
function literals(text) {
  const out = [];
  const push = (s, idx) => out.push({ s, idx });
  // className="…" / className={"…"} / className={`…`} (literal chunks)
  const attr = /className\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\}|\{\s*"([^"]*)"\s*\})/g;
  let m;
  while ((m = attr.exec(text)) !== null) push(m[1] ?? m[2] ?? m[3] ?? m[4] ?? "", m.index);
  // template literals anywhere inside a className={`…${x}…`} expression, plus
  // clsx()/cn() string arguments — both are just quoted runs of classes.
  const tmpl = /`([^`]*)`/g;
  while ((m = tmpl.exec(text)) !== null) push(m[1], m.index);
  const quoted = /(?:clsx|cn|classNames|twMerge)\s*\(([\s\S]{0,600}?)\)/g;
  while ((m = quoted.exec(text)) !== null) {
    const inner = /"([^"]*)"|'([^']*)'/g;
    let q;
    while ((q = inner.exec(m[1])) !== null) push(q[1] ?? q[2] ?? "", m.index);
  }
  return out;
}

const CLASS_RE = /(?:^|[\s:])((?:[a-z-]+:)*)([a-z]+)-([a-z]+)-([a-z0-9-]+)/g;

function scan(scales) {
  const findings = [];
  const counts = new Map();
  for (const file of sourceFiles(SRC)) {
    const text = fs.readFileSync(file, "utf8");
    for (const { s, idx } of literals(text)) {
      let m;
      CLASS_RE.lastIndex = 0;
      while ((m = CLASS_RE.exec(s)) !== null) {
        const [, , prefix, scale, rest] = m;
        if (!PREFIXES.includes(prefix)) continue;
        const keys = scales.get(scale);
        if (!keys) continue; // not a theme scale — Tailwind's own palette
        const key = rest.replace(/\/\d+$/, "");
        if (keys.has(key)) continue; // valid
        // `border-border` (DEFAULT) arrives here as prefix=border scale=border
        if (key === "" || keys.has("DEFAULT") === false) {
          // fall through: still dead unless the key matched above
        }
        const dead = `${prefix}-${scale}-${rest}`;
        const line = text.slice(0, idx).split("\n").length;
        const nearest = [...keys].sort().join(", ");
        findings.push({ file: path.relative(ROOT, file).replace(/\\/g, "/"), line, dead, scale, nearest });
        counts.set(dead, (counts.get(dead) ?? 0) + 1);
      }
    }
  }
  return { findings, counts };
}

const scales = declaredScales();
let { findings, counts } = scan(scales);
// An allowlisted name is one we have decided NOT to rename, each with its
// reason on the record — the shape check-generated-classes.js already uses.
// It is still dead; it is just dead on purpose and written down.
const allow = fs.existsSync(ALLOWLIST) ? JSON.parse(fs.readFileSync(ALLOWLIST, "utf8")) : {};
const allowed = findings.filter((f) => allow[f.dead]);
findings = findings.filter((f) => !allow[f.dead]);
counts = new Map([...counts].filter(([k]) => !allow[k]));
const listOnly = process.argv.includes("--list");

if (counts.size > 0) {
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  console.error("\ntheme-classes: utilities naming a token that does not exist\n");
  for (const [dead, n] of sorted) {
    const scale = dead.split("-")[1];
    console.error(`  ${String(n).padStart(4)}x  ${dead}`.padEnd(46) + `valid keys for "${scale}": ${[...scales.get(scale)].sort().join(", ")}`);
  }
  console.error(`\n  ${findings.length} uses of ${counts.size} dead class names, in ${new Set(findings.map((f) => f.file)).size} files.`);
  if (listOnly) {
    console.error("\nby file:");
    for (const f of findings) console.error(`  ${f.file}:${f.line}  ${f.dead}`);
  } else {
    console.error("\n  Each renders NOTHING — the element inherits its ancestor's colour.");
    console.error("  Rename to the utility carrying the same CSS variable. Run with --list for every site.\n");
    process.exit(1);
  }
} else {
  const n = Object.keys(allow).length;
  console.log(`theme-classes: 0 dead theme utilities (${scales.size} scales policed: ${[...scales.keys()].join(", ")})`
    + (n ? `; ${allowed.length} use(s) of ${n} allowlisted name(s), each with a reason` : ""));
}
