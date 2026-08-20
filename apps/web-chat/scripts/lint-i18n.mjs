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
import { ESLint } from "eslint";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const BASELINE = resolve(appRoot, "i18n-baseline.json");

const args = new Set(process.argv.slice(2));
const update = args.has("--update");
const report = args.has("--report");

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
    if (!m.ruleId) continue;
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
