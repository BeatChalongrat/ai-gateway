#!/usr/bin/env node
// i18n ratchet.
//
// Dictionary PARITY tests prove the two dictionaries hold the same keys.
// They are blind to a string that never went through a dictionary at all —
// which is how RecordingBar.tsx still renders hardcoded English while an
// unused `voicebar.speakNow` key sits in dict.ts. This closes that hole at
// write time.
//
// Ratchet, not big bang: ~200 strings already exist, and a rule that turns
// the whole tree red gets disabled within a day. Existing violations are
// recorded in i18n-baseline.json and ignored; anything NEW fails.
//
// ESLint 8 has no --suppress-all (that arrived in 9.24), so the baseline is
// a checked-in list rather than a tool feature — the fallback the task
// prescribes. Entries are keyed by file + message + the offending string,
// NOT by line number, so ordinary edits above a known violation do not
// spuriously fail the build.
//
//   node scripts/lint-i18n.mjs           # check (exit 1 on new violations)
//   node scripts/lint-i18n.mjs --update  # re-record the baseline
//   node scripts/lint-i18n.mjs --report  # print the full inventory
//
// AM11 — the check also holds the dictionaries to each other: a key that is
// in one language's dictionary and not in the other's fails it, with the
// keys listed (dictionaryParity below). The ratchet itself is unchanged.
import { ESLint } from "eslint";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const BASELINE = resolve(appRoot, "i18n-baseline.json");

const args = new Set(process.argv.slice(2));
const update = args.has("--update");
const report = args.has("--report");

// ── Dictionary parity (AM11) ────────────────────────────────────────────
//
// A string exists in every language or it does not exist. The types already
// say so (a key in one dictionary only fails tsc) and so do the tests; this
// is the same rule at the place the build already stops for i18n, and it
// says WHICH keys, and in which file. The dictionaries are read from their
// source, not run: each is an object literal, or a spread of the object
// literals of the files it imports (Agent Market's is made of six parts).
// A dictionary that is written some other way is a failure, not a skip —
// a check that quietly stops checking is worse than none.

/** The two dictionaries of each surface, by the names they have in their file. */
const DICTIONARIES = [
  { surface: "the app", file: "src/lib/i18n/dict.ts", languages: ["en", "th"] },
  { surface: "Agent Market", file: "src/features/agent-market/i18n.ts", languages: ["th", "en"] },
];

const parsed = new Map();
function sourceOf(file) {
  if (!parsed.has(file)) {
    parsed.set(file, ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS));
  }
  return parsed.get(file);
}

/** An expression with `as const`, `satisfies …`, a cast and parentheses taken off. */
function bare(node) {
  let n = node;
  while (
    n &&
    (ts.isAsExpression(n) || ts.isSatisfiesExpression(n) || ts.isParenthesizedExpression(n) || ts.isTypeAssertionExpression(n) || ts.isNonNullExpression(n))
  ) {
    n = n.expression;
  }
  return n;
}

/** The keys of `const <name> = { … }` in `file`: key → the file that writes it. */
function keysOf(file, name) {
  const source = sourceOf(file);
  const where = relative(appRoot, file).replace(/\\/g, "/");
  let initializer;
  const imported = new Map();
  for (const statement of source.statements) {
    if (ts.isVariableStatement(statement)) {
      for (const d of statement.declarationList.declarations) {
        if (ts.isIdentifier(d.name) && d.name.text === name) initializer = d.initializer;
      }
    }
    const names = ts.isImportDeclaration(statement) ? statement.importClause?.namedBindings : undefined;
    if (names && ts.isNamedImports(names) && ts.isStringLiteral(statement.moduleSpecifier) && statement.moduleSpecifier.text.startsWith(".")) {
      const from = resolve(dirname(file), `${statement.moduleSpecifier.text}.ts`);
      for (const el of names.elements) imported.set(el.name.text, { file: from, name: (el.propertyName ?? el.name).text });
    }
  }
  const object = bare(initializer);
  if (!object || !ts.isObjectLiteralExpression(object)) {
    throw new Error(`${where}: '${name}' is not an object literal — the parity check cannot read it`);
  }
  const keys = new Map();
  for (const property of object.properties) {
    if (ts.isSpreadAssignment(property)) {
      const part = bare(property.expression);
      if (!ts.isIdentifier(part)) throw new Error(`${where}: '${name}' spreads something that is not a name`);
      const from = imported.get(part.text) ?? { file, name: part.text };
      for (const [key, origin] of keysOf(from.file, from.name)) keys.set(key, origin);
    } else if (
      (ts.isPropertyAssignment(property) || ts.isShorthandPropertyAssignment(property)) &&
      (ts.isIdentifier(property.name) || ts.isStringLiteralLike(property.name) || ts.isNumericLiteral(property.name))
    ) {
      keys.set(property.name.text, where);
    } else {
      throw new Error(`${where}: '${name}' has a key that is not written out (computed, a method or an accessor)`);
    }
  }
  return keys;
}

/** Every key that is in one dictionary of a surface and not in the other. Prints what it found; true when all agree. */
function dictionaryParity() {
  let ok = true;
  const summary = [];
  for (const { surface, file, languages } of DICTIONARIES) {
    const path = resolve(appRoot, file);
    let first, second;
    try {
      first = keysOf(path, languages[0]);
      second = keysOf(path, languages[1]);
    } catch (err) {
      ok = false;
      console.error(`\ni18n parity: ${surface} (${file}) could not be read.\n  ${err instanceof Error ? err.message : String(err)}\n`);
      continue;
    }
    const only = (a, b) => [...a.keys()].filter((key) => !b.has(key)).sort();
    const onlyFirst = only(first, second);
    const onlySecond = only(second, first);
    if (onlyFirst.length + onlySecond.length === 0) {
      summary.push(`${surface} ${first.size} keys (${languages[0]} = ${languages[1]})`);
      continue;
    }
    ok = false;
    console.error(`\ni18n parity: ${surface} (${file}) — ${onlyFirst.length + onlySecond.length} key(s) in one dictionary only.\n`);
    for (const [have, lack, keys, origin] of [
      [languages[0], languages[1], onlyFirst, first],
      [languages[1], languages[0], onlySecond, second],
    ]) {
      if (!keys.length) continue;
      console.error(`  in ${have} only — ${lack} has no such key:`);
      for (const key of keys) console.error(`    ${key}    (${origin.get(key)})`);
    }
    console.error("\nA string exists in both languages or it does not exist: write the missing one, or remove the key from both.\n");
  }
  if (summary.length) console.log(`i18n parity: ${summary.join(" · ")}.`);
  return ok;
}

// Checked on every plain run. `--update` and `--report` are not checks: they
// record or list what the ratchet found and leave with their own exit code.
if (!update && !report && !dictionaryParity()) process.exitCode = 1;

const eslint = new ESLint({
  useEslintrc: false,
  overrideConfigFile: resolve(appRoot, ".eslintrc.i18n.cjs"),
  errorOnUnmatchedPattern: false,
  cwd: appRoot,
});

const results = await eslint.lintFiles(["src/**/*.tsx"]);

/** Stable identity for a violation: file + rule + the literal itself. */
const keyOf = (file, m) => `${file}::${m.ruleId}::${(m.message || "").trim()}`;

const found = [];
for (const r of results) {
  const file = relative(appRoot, r.filePath).replace(/\\/g, "/");
  for (const m of r.messages) {
    // Only the i18n rule counts. ESLint also reports "Definition for rule
    // '…' was not found" for the app's own `eslint-disable` comments that
    // name rules this one-job config does not load (FIX87: 15 such
    // entries were inflating the inventory and could never be fixed).
    if (m.ruleId !== "i18next/no-literal-string") continue;
    found.push({ key: keyOf(file, m), file, line: m.line, message: m.message });
  }
}

if (update) {
  const baseline = { generated: "manual", count: found.length, keys: found.map((f) => f.key).sort() };
  writeFileSync(BASELINE, JSON.stringify(baseline, null, 2) + "\n");
  console.log(`i18n baseline recorded: ${found.length} existing violations.`);
  process.exit(0);
}

const baseline = existsSync(BASELINE)
  ? JSON.parse(readFileSync(BASELINE, "utf8"))
  : { keys: [] };
const known = new Set(baseline.keys ?? []);

// A baseline entry may legitimately cover several identical strings in one
// file; count occurrences so removing one does not "free up" a new leak.
const fresh = [];
const seen = new Map();
for (const f of found) {
  const n = (seen.get(f.key) ?? 0) + 1;
  seen.set(f.key, n);
  if (!known.has(f.key)) fresh.push(f);
}

if (report) {
  const byFile = new Map();
  for (const f of found) {
    if (!byFile.has(f.file)) byFile.set(f.file, []);
    byFile.get(f.file).push(f);
  }
  const sorted = [...byFile.entries()].sort((a, b) => b[1].length - a[1].length);
  console.log(`# i18n violation inventory — ${found.length} in ${byFile.size} files\n`);
  for (const [file, items] of sorted) {
    console.log(`## ${file} (${items.length})`);
    for (const it of items) console.log(`  ${file}:${it.line}  ${it.message}`);
    console.log("");
  }
  process.exit(0);
}

if (fresh.length > 0) {
  console.error(`\ni18n ratchet: ${fresh.length} NEW hardcoded user-visible string(s).\n`);
  for (const f of fresh) console.error(`  ${f.file}:${f.line}  ${f.message}`);
  console.error(
    "\nRoute the string through t() (src/lib/i18n/dict.ts, EN + TH), or — if it is" +
      "\ngenuinely not user-visible — refine .eslintrc.i18n.cjs rather than adding" +
      "\nit to the baseline. The baseline is a shrinking worklist, not a dumping" +
      "\nground; regenerate it only when you have REMOVED violations.\n",
  );
  process.exit(1);
}

console.log(
  `i18n ratchet: no new violations (${found.length} known, tracked in i18n-baseline.json).`,
);
