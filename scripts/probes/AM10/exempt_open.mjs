// AM10 F — what the lint's three new exemptions let through, opened.
//
// The baseline went from 105 entries to 16 partly because strings were put through the dictionary
// and partly because the rule stopped counting three kinds of literal (a key name handed to the
// shortcut formatter, a glyph on its own, the `key` of an object written inside JSX). The second
// part is a claim about the code, so it is read from the code: the tree is linted twice — once with
// the rule as it was before AM10 and once as it is now — and every literal only the old rule
// reports is printed with its file, its line and the line as written. Nothing is counted from a
// summary; the list is the result.
//
//   node scripts/probes/AM10/exempt_open.mjs        (TSV on stdout)
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..", "..", "..", "apps", "web-chat");
const req = createRequire(resolve(appRoot, "package.json"));
const { ESLint } = req("eslint");
const now = req(resolve(appRoot, ".eslintrc.i18n.cjs"));

const RULE = "i18next/no-literal-string";
const before = JSON.parse(JSON.stringify(now, (k, v) => (v instanceof RegExp ? undefined : v)));
const opts = before.rules[RULE][1];
opts.callees = { exclude: ["t", "tOutside"] };
delete opts.words;
delete opts["object-properties"];

async function lint(config) {
  const eslint = new ESLint({ useEslintrc: false, overrideConfig: config, errorOnUnmatchedPattern: false, cwd: appRoot });
  const out = new Map();
  for (const r of await eslint.lintFiles(["src/**/*.tsx"])) {
    const file = relative(appRoot, r.filePath).replace(/\\/g, "/");
    for (const m of r.messages) {
      if (m.ruleId !== RULE) continue;
      out.set(`${file}:${m.line}:${m.column}`, { file, line: m.line, column: m.column, endLine: m.endLine, endColumn: m.endColumn });
    }
  }
  return out;
}

const [was, is] = [await lint(before), await lint(now)];
const only = [...was.entries()].filter(([k]) => !is.has(k)).map(([, v]) => v);
only.sort((a, b) => (a.file === b.file ? a.line - b.line : a.file < b.file ? -1 : 1));
console.log("n\tfile:line\tthe literal\tthe line as written");
let n = 0;
for (const v of only) {
  const lines = readFileSync(resolve(appRoot, v.file), "utf8").split("\n");
  const first = lines[v.line - 1] || "";
  const literal = v.endLine === v.line ? first.slice(v.column - 1, v.endColumn - 1) : first.slice(v.column - 1);
  console.log(`${++n}\t${v.file}:${v.line}\t${literal.trim().slice(0, 60)}\t${first.trim().slice(0, 140)}`);
}
console.log(`the rule as it was reports ${was.size} · as it is ${is.size} · let through by the exemptions ${only.length}`);
