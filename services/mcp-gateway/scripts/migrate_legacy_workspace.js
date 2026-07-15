#!/usr/bin/env node
// SRAN MCP Gateway — legacy /data/workspace inventory + quarantine
// (Ecosystem Phase 15 — Ops hardening, PART D).
//
// WHY THIS EXISTS
// ---------------
// Before Phase 10 C1 the built-in filesystem/memory MCP servers were a
// SINGLE shared, UNAUTHENTICATED child rooted directly at /data/workspace.
// Any user who could reach the gateway read/wrote the SAME files and the
// SAME memory graph. C1 (lib.js) moved every built-in server to a per-user
// root — /data/workspace/{sanitizeSubDir(sub)}/files and
// /data/workspace/{sanitizeSubDir(sub)}/memory.json — reached only with a
// valid portal access JWT.
//
// The pre-C1 content still sits DIRECTLY under the volume root with NO
// ownership metadata. Nothing records which user created a given top-level
// file, so it CANNOT be safely auto-attributed to a user: moving one user's
// leftover file into another user's per-user root would be a data leak.
//
// The safe action is therefore QUARANTINE, not guess. This tool MOVES every
// top-level entry that is NOT a valid per-user root into
// /data/workspace/_legacy_pre_c1/, preserving names. Nothing is deleted,
// nothing is attributed, nothing lands inside a live user root.
//
// A valid per-user root (left UNTOUCHED) is a directory whose name is a
// canonical sanitizeSubDir() output (a fixed point of that transform) AND
// which contains the C1 layout marker — a `files/` directory and/or a
// `memory.json` file. Everything else directly under the base is legacy
// pre-C1 content.
//
// RESTORING A KNOWN OWNER (manual, admin-only)
// --------------------------------------------
// Quarantined content is never auto-restored into a user root by design —
// it came from an insecure, unowned location. An admin who KNOWS a file's
// owner can place it under that owner's root manually. Compute the root dir
// name with the SAME transform production uses:
//
//   node -e "console.log(require('./lib').sanitizeSubDir('<the-portal-sub>'))"
//
// then move the file to /data/workspace/<that-name>/files/ (create the dir
// if needed). Otherwise it stays quarantined.
//
// USAGE
// -----
//   node scripts/migrate_legacy_workspace.js [--apply] [--base <path>] [--help]
//
//   (default)     DRY RUN — reports what WOULD move; changes nothing.
//   --apply       Actually move legacy entries into the quarantine dir.
//   --base <path> Workspace root to operate on. Also read from env
//                 WORKSPACE_BASE. Defaults to the production /data/workspace
//                 (lib.js WORKSPACE_BASE). A positional arg works too.
//   --help, -h    Print this usage and exit.
//
//   npm script:   npm run migrate:legacy-workspace -- --apply
//
// The command is IDEMPOTENT: a second run finds nothing left to move, and
// valid per-user roots plus the quarantine dir itself are never touched.

const fs = require('fs');
const path = require('path');
const { WORKSPACE_BASE, sanitizeSubDir } = require('../lib');

const QUARANTINE_NAME = '_legacy_pre_c1';

// ── classification ─────────────────────────────────────────────────────────

function safeStat(p) {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

/**
 * True when `name` (a direct child of `base`) is a live per-user root the
 * migration must NEVER touch. Recognized exactly as production creates them
 * (Phase 10 C1, lib.js):
 *   - the name is a canonical sanitizeSubDir() output (fixed point), AND
 *   - the directory holds the C1 layout: a `files/` dir and/or `memory.json`.
 */
function isValidUserRoot(base, name) {
  // sanitizeSubDir is idempotent on its own outputs; a real per-user dir
  // name therefore satisfies name === sanitizeSubDir(name). A legacy name
  // that would have been hashed (path separators, dot-only, leading '_',
  // unicode, > 64 chars) fails this and is treated as legacy.
  if (name !== sanitizeSubDir(name)) return false;

  const full = path.join(base, name);
  const st = safeStat(full);
  if (!st || !st.isDirectory()) return false;

  const filesDir = safeStat(path.join(full, 'files'));
  const memoryFile = safeStat(path.join(full, 'memory.json'));
  const hasLayout =
    (filesDir && filesDir.isDirectory()) || (memoryFile && memoryFile.isFile());
  return Boolean(hasLayout);
}

/**
 * List the direct children of `base` split into `legacy` (to quarantine) and
 * `valid` (per-user roots to leave alone). The quarantine dir itself is
 * always excluded from both — it is never moved into itself.
 */
function inventory(base) {
  let entries;
  try {
    entries = fs.readdirSync(base, { withFileTypes: true });
  } catch (err) {
    if (err.code === 'ENOENT') return { legacy: [], valid: [], missing: true };
    throw err;
  }
  const legacy = [];
  const valid = [];
  for (const ent of entries) {
    if (ent.name === QUARANTINE_NAME) continue; // never touch the quarantine dir
    if (isValidUserRoot(base, ent.name)) {
      valid.push(ent.name);
    } else {
      legacy.push(ent.name);
    }
  }
  legacy.sort();
  valid.sort();
  return { legacy, valid, missing: false };
}

// ── move (collision-safe) ───────────────────────────────────────────────────

/** Pick a non-colliding target name inside `quarantineDir` for `name`. */
function resolveTarget(quarantineDir, name) {
  let candidate = path.join(quarantineDir, name);
  if (!safeStat(candidate)) return candidate;
  for (let n = 2; ; n++) {
    candidate = path.join(quarantineDir, `${name}-${n}`);
    if (!safeStat(candidate)) return candidate;
  }
}

/**
 * Run the inventory and (when apply=true) move legacy entries into the
 * quarantine dir. Returns a report: { base, quarantineDir, apply, valid[],
 * moved[{from,to}], missing }.
 */
function migrate({ base = WORKSPACE_BASE, apply = false } = {}) {
  const quarantineDir = path.join(base, QUARANTINE_NAME);
  const { legacy, valid, missing } = inventory(base);
  const moved = [];

  if (apply && legacy.length > 0) {
    fs.mkdirSync(quarantineDir, { recursive: true });
    for (const name of legacy) {
      const from = path.join(base, name);
      const to = resolveTarget(quarantineDir, name);
      fs.renameSync(from, to);
      moved.push({ from: name, to: path.basename(to) });
    }
  } else {
    // dry run — record what WOULD move, with the collision-resolved target.
    for (const name of legacy) {
      moved.push({ from: name, to: name });
    }
  }

  return { base, quarantineDir, apply, valid, legacy, moved, missing };
}

// ── CLI ─────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const opts = { apply: false, help: false, base: undefined };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--apply') opts.apply = true;
    else if (a === '--help' || a === '-h') opts.help = true;
    else if (a === '--base') opts.base = argv[++i];
    else if (a.startsWith('--base=')) opts.base = a.slice('--base='.length);
    else if (!a.startsWith('-') && opts.base === undefined) opts.base = a;
  }
  return opts;
}

const HELP = `migrate_legacy_workspace — quarantine pre-C1 /data/workspace content

  node scripts/migrate_legacy_workspace.js [--apply] [--base <path>]

  (default)      DRY RUN: report legacy entries; change nothing.
  --apply        Move legacy entries into <base>/${QUARANTINE_NAME}/ .
  --base <path>  Workspace root (or env WORKSPACE_BASE, or a positional arg).
                 Default: production ${WORKSPACE_BASE}.
  --help, -h     Show this help.

Legacy = every top-level entry that is NOT a valid per-user root. A valid
per-user root is a sanitizeSubDir-shaped dir holding files/ and/or memory.json;
those and the quarantine dir are never touched. Pre-C1 shared files lived in an
insecure, UNOWNED location and are NOT auto-restored into user roots by design.`;

function main(argv) {
  const opts = parseArgs(argv);
  if (opts.help) {
    process.stdout.write(HELP + '\n');
    return 0;
  }
  const base = opts.base || process.env.WORKSPACE_BASE || WORKSPACE_BASE;
  const report = migrate({ base, apply: opts.apply });

  const mode = report.apply ? 'APPLY' : 'DRY RUN';
  process.stdout.write(`[migrate-legacy-workspace] ${mode}  base=${report.base}\n`);
  if (report.missing) {
    process.stdout.write('  base directory does not exist — nothing to do.\n');
    return 0;
  }
  process.stdout.write(`  valid per-user roots (untouched): ${report.valid.length}\n`);
  for (const name of report.valid) process.stdout.write(`    keep  ${name}\n`);

  if (report.legacy.length === 0) {
    process.stdout.write('  legacy content: none — already migrated / clean.\n');
    return 0;
  }
  const verb = report.apply ? 'moved' : 'would move';
  process.stdout.write(
    `  legacy content (${verb} → ${QUARANTINE_NAME}/): ${report.legacy.length}\n`
  );
  for (const m of report.moved) {
    const arrow = m.from === m.to ? m.from : `${m.from} -> ${m.to}`;
    process.stdout.write(`    ${report.apply ? 'moved' : 'move'}  ${arrow}\n`);
  }
  if (!report.apply) {
    process.stdout.write('\n  Re-run with --apply to perform the moves.\n');
  }
  return 0;
}

module.exports = {
  QUARANTINE_NAME,
  isValidUserRoot,
  inventory,
  migrate,
  parseArgs,
  main
};

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
