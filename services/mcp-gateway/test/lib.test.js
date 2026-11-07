// node --test suite for the mcp-gateway core (Desktop Phase 7 PART B).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const {
  SERVERS,
  buildCatalog,
  verifyPortalJwt,
  sanitizeSubDir,
  userChildConfig,
  InstanceManager,
  TokenFetchError
} = require('../lib');

// ── helpers ────────────────────────────────────────────────────────────────

function b64url(buf) {
  return Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function mintJwt(payload, secret, alg = 'HS256') {
  const head = b64url(JSON.stringify({ alg, typ: 'JWT' }));
  const body = b64url(JSON.stringify(payload));
  const sig = b64url(crypto.createHmac('sha256', secret).update(`${head}.${body}`).digest());
  return `${head}.${body}.${sig}`;
}

const SECRET = 'test-portal-secret';
const NOW = 1_800_000_000; // seconds
const validPayload = {
  sub: 'user-1', type: 'portal', token_use: 'access',
  tenant_id: 't-1', exp: NOW + 3600
};

// ── verifyPortalJwt ────────────────────────────────────────────────────────

test('jwt: valid token is accepted with sub/tenant extracted', () => {
  const payload = verifyPortalJwt(mintJwt(validPayload, SECRET), SECRET, NOW);
  assert.equal(payload.sub, 'user-1');
  assert.equal(payload.tenant_id, 't-1');
});

test('jwt: wrong secret is rejected', () => {
  assert.equal(verifyPortalJwt(mintJwt(validPayload, 'other'), SECRET, NOW), null);
});

test('jwt: expired token is rejected', () => {
  const expired = { ...validPayload, exp: NOW - 1 };
  assert.equal(verifyPortalJwt(mintJwt(expired, SECRET), SECRET, NOW), null);
});

test('jwt: non-portal token is rejected', () => {
  const admin = { ...validPayload, type: 'admin' };
  assert.equal(verifyPortalJwt(mintJwt(admin, SECRET), SECRET, NOW), null);
});

test('jwt: alg confusion (none) is rejected', () => {
  const head = b64url(JSON.stringify({ alg: 'none', typ: 'JWT' }));
  const body = b64url(JSON.stringify(validPayload));
  assert.equal(verifyPortalJwt(`${head}.${body}.`, SECRET, NOW), null);
});

test('jwt: garbage and empty inputs are rejected without throwing', () => {
  for (const bad of [null, '', 'a.b', 'not a jwt at all', 'a.b.c.d']) {
    assert.equal(verifyPortalJwt(bad, SECRET, NOW), null);
  }
});

// C4: a refresh token carrying type=portal must NOT work as a bearer —
// mirrors the Go verifier (chat-edge-api portal_jwt.go).
test('jwt: refresh token is rejected as bearer (C4)', () => {
  const refresh = { ...validPayload, token_use: 'refresh' };
  assert.equal(verifyPortalJwt(mintJwt(refresh, SECRET), SECRET, NOW), null);
});

test('jwt: legacy access token without token_use is still accepted (Go parity)', () => {
  const { token_use, ...legacy } = validPayload;
  const payload = verifyPortalJwt(mintJwt(legacy, SECRET), SECRET, NOW);
  assert.equal(payload.sub, 'user-1');
});

// ── catalog ────────────────────────────────────────────────────────────────

test('catalog: every entry matches the desktop McpConnector contract', () => {
  const { connectors } = buildCatalog(SERVERS, 'http://localhost', 'port');
  assert.ok(connectors.length >= 4);
  for (const c of connectors) {
    assert.deepEqual(
      Object.keys(c).sort(),
      ['auth_type', 'category', 'description', 'id', 'name', 'server_url', 'tools']
    );
    assert.ok(Array.isArray(c.tools));
  }
});

test('catalog: oauth2 URLs route through the gateway per-user path', () => {
  const port = buildCatalog(SERVERS, 'http://localhost', 'port').connectors;
  const github = port.find((c) => c.id === 'github');
  assert.equal(github.auth_type, 'oauth2');
  assert.equal(github.server_url, 'http://localhost:3200/mcp/servers/github');

  const path = buildCatalog(SERVERS, 'https://sran.example', 'path').connectors;
  assert.equal(
    path.find((c) => c.id === 'slack').server_url,
    'https://sran.example/mcp/servers/slack'
  );
});

// Phase 10 C1: this test previously asserted the LEGACY direct child port
// (http://localhost:3201) — i.e. it pinned the unauthenticated bypass that
// C1 removes. none-auth servers now route through the gateway's
// JWT-verified per-user path like every other server.
test('catalog: none-auth servers route through the authenticated gateway path (C1)', () => {
  const { connectors } = buildCatalog(SERVERS, 'http://localhost', 'port');
  assert.equal(
    connectors.find((c) => c.id === 'filesystem').server_url,
    'http://localhost:3200/mcp/servers/filesystem'
  );
  const path = buildCatalog(SERVERS, 'https://sran.example', 'path').connectors;
  assert.equal(
    path.find((c) => c.id === 'memory').server_url,
    'https://sran.example/mcp/servers/memory'
  );
});

// ── InstanceManager ────────────────────────────────────────────────────────

const GITHUB = SERVERS.find((s) => s.id === 'github');
const SLACK = SERVERS.find((s) => s.id === 'slack');

function fakeChild() {
  const listeners = {};
  return {
    killed: false,
    kill() { this.killed = true; },
    on(evt, fn) { listeners[evt] = fn; },
    emit(evt) { listeners[evt]?.(); }
  };
}

function makeManager(overrides = {}) {
  const spawned = [];
  let clock = 0;
  const mgr = new InstanceManager({
    spawnChild: (server, port, token) => {
      const child = fakeChild();
      spawned.push({ server: server.id, port, token, child });
      return child;
    },
    fetchToken: async () => ({ access_token: 'tok-a', expires_at: null }),
    now: () => clock,
    idleMs: 10 * 60 * 1000,
    maxChildren: 3,
    ...overrides
  });
  return { mgr, spawned, tick: (ms) => { clock += ms; } };
}

test('manager: same user+connector reuses one child', async () => {
  const { mgr, spawned } = makeManager();
  const a = await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  const b = await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  assert.equal(spawned.length, 1);
  assert.equal(a.port, b.port);
});

test('manager: different users are isolated into separate children', async () => {
  const { mgr, spawned } = makeManager();
  const a = await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  const b = await mgr.acquire(GITHUB, 'user-2', 'Bearer y');
  assert.equal(spawned.length, 2);
  assert.notEqual(a.port, b.port);
});

test('manager: same user, different connectors → separate children', async () => {
  const { mgr, spawned } = makeManager();
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  await mgr.acquire(SLACK, 'user-1', 'Bearer x');
  assert.equal(spawned.length, 2);
});

test('manager: idle children are evicted by sweep', async () => {
  const { mgr, spawned, tick } = makeManager();
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  tick(11 * 60 * 1000); // beyond the 10-min idle window
  mgr.sweep();
  assert.equal(mgr.entries.size, 0);
  assert.equal(spawned[0].child.killed, true);
  // Next call respawns.
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  assert.equal(spawned.length, 2);
});

test('manager: token refresh failure evicts and rethrows', async () => {
  let fail = false;
  const { mgr, spawned } = makeManager({
    fetchToken: async () => {
      if (fail) throw new TokenFetchError(401);
      // Short-lived token so the second acquire re-fetches.
      return { access_token: 'tok-a', expires_at: new Date(30_000).toISOString() };
    }
  });
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  assert.equal(mgr.entries.size, 1);

  fail = true;
  await assert.rejects(
    () => mgr.acquire(GITHUB, 'user-1', 'Bearer x'),
    (err) => err instanceof TokenFetchError && err.status === 401
  );
  assert.equal(mgr.entries.size, 0);
  assert.equal(spawned[0].child.killed, true);
});

test('manager: rotated token respawns the child with the new env', async () => {
  let token = 'tok-a';
  const { mgr, spawned } = makeManager({
    fetchToken: async () => ({
      access_token: token,
      expires_at: new Date(30_000).toISOString() // always near-expired → refetch
    })
  });
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  token = 'tok-b';
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  assert.equal(spawned.length, 2);
  assert.equal(spawned[0].child.killed, true);
  assert.equal(spawned[1].token, 'tok-b');
});

test('manager: global cap evicts the least-recently-used child', async () => {
  const { mgr, spawned, tick } = makeManager(); // maxChildren: 3
  await mgr.acquire(GITHUB, 'user-1', 'Bearer a'); tick(1000);
  await mgr.acquire(GITHUB, 'user-2', 'Bearer b'); tick(1000);
  await mgr.acquire(GITHUB, 'user-3', 'Bearer c'); tick(1000);
  await mgr.acquire(GITHUB, 'user-4', 'Bearer d');
  assert.equal(mgr.entries.size, 3);
  assert.equal(spawned[0].child.killed, true); // user-1 was LRU
  assert.equal(mgr.entries.has(mgr.key('user-1', 'github')), false);
  assert.equal(mgr.entries.has(mgr.key('user-4', 'github')), true);
});

test('manager: child exit drops the entry so the next call respawns', async () => {
  const { mgr, spawned } = makeManager();
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  spawned[0].child.emit('exit');
  assert.equal(mgr.entries.size, 0);
  await mgr.acquire(GITHUB, 'user-1', 'Bearer x');
  assert.equal(spawned.length, 2);
});

// ── C2: concurrent first-acquire must not leak a child + port ─────────────

test('manager: concurrent acquires for one key coalesce into ONE child (C2)', async () => {
  const { mgr, spawned } = makeManager({
    // Force an await gap like the real broker round-trip.
    fetchToken: async () => {
      await new Promise((r) => setImmediate(r));
      return { access_token: 'tok-a', expires_at: null };
    }
  });
  const [a, b] = await Promise.all([
    mgr.acquire(GITHUB, 'user-1', 'Bearer x'),
    mgr.acquire(GITHUB, 'user-1', 'Bearer x')
  ]);
  assert.equal(spawned.length, 1); // pre-fix: 2 — the first child was orphaned
  assert.equal(a, b);
  assert.equal(mgr.usedPorts.size, 1); // pre-fix: 2 — one port leaked forever
  assert.equal(mgr.entries.size, 1);
  assert.equal(mgr.pending.size, 0); // no dangling in-flight promise
});

test('manager: concurrent acquires for DIFFERENT users still isolate (C2)', async () => {
  const { mgr, spawned } = makeManager({
    fetchToken: async () => {
      await new Promise((r) => setImmediate(r));
      return { access_token: 'tok-a', expires_at: null };
    }
  });
  const [a, b] = await Promise.all([
    mgr.acquire(GITHUB, 'user-1', 'Bearer x'),
    mgr.acquire(GITHUB, 'user-2', 'Bearer y')
  ]);
  assert.equal(spawned.length, 2);
  assert.notEqual(a.port, b.port);
});

// ── C1: none-auth (built-in) servers are per-user, no provider token ──────

const FILESYSTEM = SERVERS.find((s) => s.id === 'filesystem');
const MEMORY = SERVERS.find((s) => s.id === 'memory');

test('manager: none-auth servers spawn per user without a broker fetch (C1)', async () => {
  const { mgr, spawned } = makeManager({
    fetchToken: async () => {
      throw new Error('must not fetch a provider token for a none-auth server');
    }
  });
  const a = await mgr.acquire(FILESYSTEM, 'user-1', 'Bearer x');
  const b = await mgr.acquire(FILESYSTEM, 'user-2', 'Bearer y');
  assert.equal(spawned.length, 2); // pre-fix: none-auth never hit the manager at all
  assert.notEqual(a.port, b.port);
  assert.equal(spawned[0].token, null); // no provider token in the child env
  // Same user reuses their child.
  const again = await mgr.acquire(FILESYSTEM, 'user-1', 'Bearer x');
  assert.equal(again.port, a.port);
  assert.equal(spawned.length, 2);
});

test('isolation: filesystem roots are per-user and traversal-safe (C1)', () => {
  const a = userChildConfig(FILESYSTEM, 'user-1');
  const b = userChildConfig(FILESYSTEM, 'user-2');
  assert.equal(a.args.at(-1), '/data/workspace/user-1/files');
  assert.notEqual(a.args.at(-1), b.args.at(-1));
  assert.deepEqual(a.dirs, ['/data/workspace/user-1/files']);

  // A hostile sub must not escape the workspace base.
  for (const evil of ['../../etc', '..', 'a/b', 'a\\b', '.hidden', '']) {
    const cfg = userChildConfig(FILESYSTEM, evil);
    const root = cfg.args.at(-1);
    assert.ok(root.startsWith('/data/workspace/u-'), `sub ${JSON.stringify(evil)} must hash`);
    assert.ok(!root.includes('..'));
  }
  // Distinct hostile subs stay distinct (hashing, not stripping).
  assert.notEqual(
    userChildConfig(FILESYSTEM, 'a/b').args.at(-1),
    userChildConfig(FILESYSTEM, 'a\\b').args.at(-1)
  );
});

test('isolation: memory graph file is per-user, outside the filesystem root (C1)', () => {
  const cfg = userChildConfig(MEMORY, 'user-1');
  assert.equal(cfg.env.MEMORY_FILE_PATH, '/data/workspace/user-1/memory.json');
  assert.notEqual(
    cfg.env.MEMORY_FILE_PATH,
    userChildConfig(MEMORY, 'user-2').env.MEMORY_FILE_PATH
  );
  // The filesystem server for the same user is rooted at {root}/files, so
  // its tools cannot rewrite the memory graph.
  const fsRoot = userChildConfig(FILESYSTEM, 'user-1').args.at(-1);
  assert.ok(!cfg.env.MEMORY_FILE_PATH.startsWith(`${fsRoot}/`));
});

test('isolation: oauth2 servers keep their static args (no user_scope)', () => {
  const cfg = userChildConfig(GITHUB, 'user-1');
  assert.deepEqual(cfg.args, GITHUB.args);
  assert.deepEqual(cfg.dirs, []);
});

test('sanitizeSubDir: clean ids pass through; everything else hashes deterministically', () => {
  assert.equal(sanitizeSubDir('3f2a9c1e-aaaa-bbbb-cccc-121212121212'),
    '3f2a9c1e-aaaa-bbbb-cccc-121212121212');
  assert.equal(sanitizeSubDir('user_1.x-Y'), 'user_1.x-Y');
  const hashed = sanitizeSubDir('a/b');
  assert.match(hashed, /^u-[0-9a-f]{32}$/);
  assert.equal(hashed, sanitizeSubDir('a/b')); // deterministic
  assert.notEqual(hashed, sanitizeSubDir('a_b'));
});
