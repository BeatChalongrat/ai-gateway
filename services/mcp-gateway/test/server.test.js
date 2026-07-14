// node --test suite for the HTTP proxy surface (Phase 10 C1/C3/C5).
// Boots the real express app (createApp) against a fake InstanceManager and
// a real local HTTP "upstream child", then exercises it with fetch.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');

const { createApp } = require('../server');
const { SERVERS } = require('../lib');

// ── helpers ────────────────────────────────────────────────────────────────

function b64url(buf) {
  return Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function mintJwt(payload, secret) {
  const head = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64url(JSON.stringify(payload));
  const sig = b64url(crypto.createHmac('sha256', secret).update(`${head}.${body}`).digest());
  return `${head}.${body}.${sig}`;
}

const SECRET = 'test-portal-secret';

function accessToken(sub = 'user-1', extra = {}) {
  const now = Math.floor(Date.now() / 1000);
  return mintJwt({
    sub, type: 'portal', token_use: 'access', tenant_id: 't-1',
    exp: now + 3600, ...extra
  }, SECRET);
}

/** Start an upstream "supergateway child" on 127.0.0.1:0. */
function startUpstream(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () =>
      resolve({ srv, port: srv.address().port }));
  });
}

/** Boot the app with a fake manager pinned to the given upstream port. */
function startApp(upstreamPort) {
  const manager = {
    calls: [],
    async acquire(server, sub, authHeader) {
      this.calls.push({ server: server.id, sub, authHeader });
      return { port: upstreamPort };
    }
  };
  const app = createApp({
    servers: SERVERS,
    manager,
    portalJwtSecret: SECRET,
    retryAttempts: 1,
    retryDelayMs: 0
  });
  return new Promise((resolve) => {
    const srv = app.listen(0, '127.0.0.1', () =>
      resolve({ srv, manager, base: `http://127.0.0.1:${srv.address().port}` }));
  });
}

function withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`timeout: ${label}`)), ms);
    })
  ]);
}

const RPC_BODY = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' });

// ── C1: none-auth servers require a portal JWT ─────────────────────────────

test('proxy: none-auth server WITHOUT a token → 401, nothing proxied (C1)', async () => {
  let upstreamHits = 0;
  const up = await startUpstream((_req, res) => { upstreamHits++; res.end('{}'); });
  const gw = await startApp(up.port);
  try {
    const resp = await fetch(`${gw.base}/mcp/servers/filesystem/mcp`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: RPC_BODY
    });
    assert.equal(resp.status, 401); // pre-fix: 200, proxied to the shared child
    const body = await resp.json();
    assert.equal(body.error.code, -32001);
    assert.equal(upstreamHits, 0);
    assert.equal(gw.manager.calls.length, 0);
  } finally {
    gw.srv.close(); up.srv.close();
  }
});

test('proxy: none-auth server rejects a refresh token as bearer (C1+C4)', async () => {
  const up = await startUpstream((_req, res) => res.end('{}'));
  const gw = await startApp(up.port);
  try {
    const resp = await fetch(`${gw.base}/mcp/servers/memory/mcp`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${accessToken('user-1', { token_use: 'refresh' })}`
      },
      body: RPC_BODY
    });
    assert.equal(resp.status, 401);
  } finally {
    gw.srv.close(); up.srv.close();
  }
});

test('proxy: none-auth server with a valid access token proxies PER USER (C1)', async () => {
  const up = await startUpstream((req, res) => {
    res.setHeader('content-type', 'application/json');
    res.setHeader('mcp-session-id', 'sess-42');
    res.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { ok: true } }));
  });
  const gw = await startApp(up.port);
  try {
    const resp = await fetch(`${gw.base}/mcp/servers/filesystem/mcp`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${accessToken('user-77')}`
      },
      body: RPC_BODY
    });
    assert.equal(resp.status, 200);
    assert.equal(resp.headers.get('mcp-session-id'), 'sess-42');
    const body = await resp.json();
    assert.equal(body.result.ok, true);
    // The user's OWN instance was acquired — the per-user isolation seam.
    assert.deepEqual(
      gw.manager.calls.map((c) => [c.server, c.sub]),
      [['filesystem', 'user-77']]
    );
  } finally {
    gw.srv.close(); up.srv.close();
  }
});

// ── C3: the proxy must stream, not buffer ──────────────────────────────────

test('proxy: SSE upstream reaches the client incrementally (C3)', async () => {
  let releaseSecond;
  const secondChunkGate = new Promise((r) => { releaseSecond = r; });

  const up = await startUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write('data: {"first":1}\n\n');
    // Hold the stream open until the CLIENT confirms it saw chunk one —
    // a buffering proxy (arrayBuffer()) can never get past this gate.
    await secondChunkGate;
    res.write('data: {"second":2}\n\n');
    res.end();
  });
  const gw = await startApp(up.port);
  try {
    const resp = await fetch(`${gw.base}/mcp/servers/memory/mcp`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'text/event-stream',
        authorization: `Bearer ${accessToken()}`
      },
      body: RPC_BODY
    });
    assert.equal(resp.status, 200);
    assert.equal(resp.headers.get('content-type'), 'text/event-stream');

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    // Pre-fix this times out: res.send(await upstream.arrayBuffer()) emits
    // nothing until the upstream closes, which is gated on this very read.
    const first = await withTimeout(reader.read(), 3000,
      'first SSE chunk never arrived — the proxy is buffering the stream');
    assert.match(decoder.decode(first.value), /"first":1/);

    releaseSecond();
    let rest = '';
    for (;;) {
      const { done, value } = await withTimeout(reader.read(), 3000, 'rest of stream');
      if (done) break;
      rest += decoder.decode(value);
    }
    assert.match(rest, /"second":2/);
  } finally {
    releaseSecond(); // unblock the upstream handler on failure paths
    gw.srv.close(); up.srv.close();
  }
});

// ── C5: /health must not leak instance counts ──────────────────────────────

test('health: unauthenticated /health has no user_instances field (C5)', async () => {
  const up = await startUpstream((_req, res) => res.end('{}'));
  const gw = await startApp(up.port);
  try {
    const resp = await fetch(`${gw.base}/health`);
    assert.equal(resp.status, 200);
    const body = await resp.json();
    assert.equal(body.status, 'ok');
    assert.ok(!('user_instances' in body)); // pre-fix: leaked manager.entries.size
  } finally {
    gw.srv.close(); up.srv.close();
  }
});
