// Where did a token actually land? Asks BOTH stores by name and prints
// the row count each one was read from.
//
// This exists because of a near-miss in FIX56: the operator hint pointed
// at %APPDATA%/SRAN Desktop/data.db, which belongs to the RETIRED WPF
// app. Grepping it finds nothing and reads as proof of privacy. Any
// check that names a store by path has to print how many rows it
// actually read, or it can prove anything by looking in an empty drawer.
//
// Usage:
//   node check_stores.js <token> [<token> ...]
//
// Prints one line per (token, store). Exit code is 0 either way — this
// reports, it does not judge; the probe that produced the tokens says
// which ones are supposed to be found.

const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { DatabaseSync } = require("node:sqlite");

const USER = "f1848000-0000-4000-8000-000000000048";
const PG = ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-t", "-A", "-c"];

// The ELECTRON sidecar's store. Not "SRAN Desktop" — that directory is
// the retired WPF app's and still holds stale notes.
const LOCAL_DB = path.join(
  process.env.APPDATA || "",
  "SranDesktop-Electron",
  "data.db",
);

function serverMatches(token) {
  const total = Number(String(execFileSync("docker", [
    ...PG, `select count(*) from chat.user_memories where status='active';`,
  ])).trim());
  const hit = Number(String(execFileSync("docker", [
    ...PG,
    `select count(*) from chat.user_memories where content like '%${token}%';`,
  ])).trim());
  return { store: `postgres chat.user_memories`, rowsRead: total, matches: hit };
}

function localMatches(token) {
  const db = new DatabaseSync(LOCAL_DB, { readOnly: true });
  try {
    const total = db.prepare("select count(*) as n from agent_notes").get().n;
    const hit = db
      .prepare("select count(*) as n from agent_notes where content like ?")
      .get(`%${token}%`).n;
    return { store: `sqlite ${LOCAL_DB} -> agent_notes`, rowsRead: total, matches: hit };
  } finally {
    db.close();
  }
}

for (const token of process.argv.slice(2)) {
  for (const r of [localMatches(token), serverMatches(token)]) {
    console.log(
      `${token.padEnd(20)} ${String(r.matches).padStart(3)} match(es) ` +
      `in ${r.rowsRead} rows — ${r.store}`,
    );
  }
  console.log();
}
