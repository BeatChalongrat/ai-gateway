// node --test suite for the legacy /data/workspace quarantine migration
// (Ecosystem Phase 15 PART D). Builds a fake workspace tree in a temp dir,
// runs the migration --apply against it, and asserts ONLY legacy content is
// quarantined while valid per-user roots stay put — using the REAL
// sanitizeSubDir from lib.js so "valid root" is recognized exactly as
// production creates it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { sanitizeSubDir } = require('../lib');
const {
  QUARANTINE_NAME,
  isValidUserRoot,
  inventory,
  migrate
} = require('../scripts/migrate_legacy_workspace');

// ── fixture ──────────────────────────────────────────────────────────────────

function mkbase() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-ws-'));

  // (a) VALID per-user roots — exactly the C1 layout lib.js produces.
  //     A clean uuid-ish sub passes sanitizeSubDir unchanged.
  const cleanSub = '3f2a9c1e-aaaa-bbbb-cccc-121212121212';
  assert.equal(sanitizeSubDir(cleanSub), cleanSub); // fixed point (real prod name)
  fs.mkdirSync(path.join(base, cleanSub, 'files'), { recursive: true });
  fs.writeFileSync(path.join(base, cleanSub, 'files', 'note.txt'), 'hi');
  fs.writeFileSync(path.join(base, cleanSub, 'memory.json'), '{}');

  //     A hashed sub (hostile/unclean id) — its dir name is the hash output,
  //     which is itself a fixed point of sanitizeSubDir.
  const hashedName = sanitizeSubDir('a/b');
  assert.match(hashedName, /^u-[0-9a-f]{32}$/);
  fs.mkdirSync(path.join(base, hashedName, 'files'), { recursive: true });

  //     A per-user root with only memory.json (filesystem never used).
  const memOnly = 'user_only_memory';
  assert.equal(sanitizeSubDir(memOnly), memOnly);
  fs.mkdirSync(path.join(base, memOnly), { recursive: true });
  fs.writeFileSync(path.join(base, memOnly, 'memory.json'), '{}');

  const validRoots = [cleanSub, hashedName, memOnly];

  // (b) LEGACY content sitting directly under the root (pre-C1 shared).
  //   - top-level files
  fs.writeFileSync(path.join(base, 'shared-notes.txt'), 'legacy');
  fs.writeFileSync(path.join(base, 'memory.json'), '{"legacy":true}'); // shared graph
  //   - a top-level dir that is NOT a per-user root (no files/ + no memory.json)
  fs.mkdirSync(path.join(base, 'uploads'), { recursive: true });
  fs.writeFileSync(path.join(base, 'uploads', 'a.bin'), 'x');
  //   - a sanitizeSubDir-shaped dir with NO C1 layout marker → still legacy
  fs.mkdirSync(path.join(base, 'docs'), { recursive: true });
  fs.writeFileSync(path.join(base, 'docs', 'readme.md'), 'legacy');

  const legacyNames = ['shared-notes.txt', 'memory.json', 'uploads', 'docs'];

  return { base, validRoots, legacyNames };
}

function rm(base) {
  fs.rmSync(base, { recursive: true, force: true });
}

// ── tests ────────────────────────────────────────────────────────────────────

test('classifier: recognizes valid per-user roots and rejects legacy', () => {
  const { base, validRoots, legacyNames } = mkbase();
  try {
    for (const name of validRoots) {
      assert.equal(isValidUserRoot(base, name), true, `${name} should be a valid root`);
    }
    for (const name of legacyNames) {
      assert.equal(isValidUserRoot(base, name), false, `${name} should be legacy`);
    }
    // The quarantine dir name is never a valid root (leading '_' → not a
    // sanitizeSubDir fixed point).
    assert.equal(isValidUserRoot(base, QUARANTINE_NAME), false);
  } finally {
    rm(base);
  }
});

test('dry run: reports legacy, moves nothing', () => {
  const { base, validRoots, legacyNames } = mkbase();
  try {
    const report = migrate({ base, apply: false });
    assert.equal(report.apply, false);
    assert.deepEqual(report.legacy.sort(), [...legacyNames].sort());
    assert.deepEqual(report.valid.sort(), [...validRoots].sort());
    // Nothing actually moved.
    assert.equal(fs.existsSync(path.join(base, QUARANTINE_NAME)), false);
    for (const name of legacyNames) {
      assert.equal(fs.existsSync(path.join(base, name)), true);
    }
  } finally {
    rm(base);
  }
});

test('apply: quarantines ONLY legacy, leaves valid roots untouched', () => {
  const { base, validRoots, legacyNames } = mkbase();
  try {
    const report = migrate({ base, apply: true });
    assert.equal(report.apply, true);

    const qDir = path.join(base, QUARANTINE_NAME);
    assert.equal(fs.existsSync(qDir), true);

    // Every legacy entry left the root and now lives in quarantine.
    for (const name of legacyNames) {
      assert.equal(fs.existsSync(path.join(base, name)), false, `${name} should be gone from root`);
      assert.equal(fs.existsSync(path.join(qDir, name)), true, `${name} should be quarantined`);
    }
    // Legacy dir contents are preserved (not just the dir name).
    assert.equal(
      fs.readFileSync(path.join(qDir, 'uploads', 'a.bin'), 'utf8'),
      'x'
    );

    // Valid per-user roots are exactly where they were, contents intact.
    for (const name of validRoots) {
      assert.equal(fs.existsSync(path.join(base, name)), true, `${name} must be untouched`);
    }
    const cleanSub = validRoots[0];
    assert.equal(
      fs.readFileSync(path.join(base, cleanSub, 'files', 'note.txt'), 'utf8'),
      'hi'
    );

    // The valid root's own memory.json was NOT confused with the legacy one.
    assert.equal(fs.readFileSync(path.join(base, cleanSub, 'memory.json'), 'utf8'), '{}');
    assert.equal(
      fs.readFileSync(path.join(qDir, 'memory.json'), 'utf8'),
      '{"legacy":true}'
    );
  } finally {
    rm(base);
  }
});

test('idempotent: a second apply moves nothing; dry run reports clean', () => {
  const { base } = mkbase();
  try {
    const first = migrate({ base, apply: true });
    assert.ok(first.moved.length > 0);

    const second = migrate({ base, apply: true });
    assert.equal(second.legacy.length, 0, 'nothing left to quarantine');
    assert.equal(second.moved.length, 0);

    const dry = migrate({ base, apply: false });
    assert.equal(dry.legacy.length, 0);

    // The quarantine dir is itself skipped — never moved into itself.
    const qDir = path.join(base, QUARANTINE_NAME);
    assert.equal(fs.existsSync(qDir), true);
    assert.equal(fs.existsSync(path.join(qDir, QUARANTINE_NAME)), false);
  } finally {
    rm(base);
  }
});

test('collision: same-named legacy across runs is suffixed, never overwritten', () => {
  const { base } = mkbase();
  try {
    migrate({ base, apply: true }); // quarantines the first shared-notes.txt

    // A NEW legacy file with a name already present in quarantine appears.
    fs.writeFileSync(path.join(base, 'shared-notes.txt'), 'second');
    const report = migrate({ base, apply: true });

    const qDir = path.join(base, QUARANTINE_NAME);
    assert.equal(fs.readFileSync(path.join(qDir, 'shared-notes.txt'), 'utf8'), 'legacy');
    assert.equal(fs.readFileSync(path.join(qDir, 'shared-notes.txt-2'), 'utf8'), 'second');
    assert.deepEqual(report.moved, [{ from: 'shared-notes.txt', to: 'shared-notes.txt-2' }]);
  } finally {
    rm(base);
  }
});

test('missing base: reported, no throw', () => {
  const report = migrate({ base: path.join(os.tmpdir(), 'does-not-exist-xyz-123'), apply: true });
  assert.equal(report.missing, true);
  assert.equal(report.legacy.length, 0);
});

test('empty base: no legacy, no quarantine dir created', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-ws-empty-'));
  try {
    const report = migrate({ base, apply: true });
    assert.equal(report.legacy.length, 0);
    assert.equal(fs.existsSync(path.join(base, QUARANTINE_NAME)), false);
    assert.equal(inventory(base).legacy.length, 0);
  } finally {
    rm(base);
  }
});
