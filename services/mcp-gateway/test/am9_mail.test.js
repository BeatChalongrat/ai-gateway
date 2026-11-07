// AM9 — the mail connector ("send as me").
//   * it is no model's tool: absent from the catalogue every model's tools are
//     resolved from, and the proxy refuses a call without chat-edge's token;
//   * the proof the card asked for first: the real server process sends a
//     real .xlsx as a real attachment — the message the Gmail endpoint
//     receives is parsed back and the attachment's bytes are the file's;
//   * the person's token is only in the child's environment: never in what
//     the server prints, never in an error.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');

const { createApp } = require('../server');
const { SERVERS, buildCatalog, forModels } = require('../lib');

const SECRET = 'test-portal-secret';
const INTERNAL = 'chat-edge-internal-token-for-tests';
const XLSX = path.join(__dirname, '..', '..', '..', 'backend', 'agent_market', 'tests', 'fixtures', 'print', 'am8_bank_paper.xlsx');

function b64url(buf) {
  return Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function accessToken(sub = 'user-1') {
  const head = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64url(JSON.stringify({ sub, type: 'portal', token_use: 'access', tenant_id: 't-1', exp: Math.floor(Date.now() / 1000) + 3600 }));
  return `${head}.${body}.${b64url(crypto.createHmac('sha256', SECRET).update(`${head}.${body}`).digest())}`;
}
function listen(handler) {
  return new Promise((resolve) => {
    const srv = http.createServer(handler);
    srv.listen(0, '127.0.0.1', () => resolve({ srv, port: srv.address().port }));
  });
}

test('the mail connector is no model\'s tool: not in the catalogue', () => {
  const mail = SERVERS.find((s) => s.id === 'mail');
  assert.ok(mail, 'a mail server is registered');
  assert.equal(mail.auth_type, 'oauth2');
  assert.equal(mail.models, false);
  const ids = buildCatalog(SERVERS, 'http://localhost', 'port').connectors.map((c) => c.id);
  assert.ok(!ids.includes('mail'), `catalogue: ${ids}`);
  for (const c of buildCatalog(SERVERS, 'http://localhost', 'port').connectors) {
    for (const tool of c.tools) assert.ok(!/mail|send_message/i.test(tool), `a mail tool in the catalogue: ${c.id}/${tool}`);
  }
  assert.deepEqual(SERVERS.filter((s) => !forModels(s)).map((s) => s.id), ['mail']);
});

test('the proxy refuses the mail connector to a caller without chat-edge\'s token, even with a valid portal token', async () => {
  const up = await listen((req, res) => { res.setHeader('content-type', 'application/json'); res.end('{"jsonrpc":"2.0","id":1,"result":{}}'); });
  const manager = { calls: [], async acquire(server, sub) { this.calls.push(server.id); return { port: up.port }; } };
  const app = createApp({ servers: SERVERS, manager, portalJwtSecret: SECRET, internalToken: INTERNAL, retryAttempts: 1, retryDelayMs: 0 });
  const srv = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  const base = `http://127.0.0.1:${srv.address().port}/mcp/servers/mail/mcp`;
  const body = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
  try {
    const headers = { authorization: `Bearer ${accessToken()}`, 'content-type': 'application/json', accept: 'application/json' };
    assert.equal((await fetch(base, { method: 'POST', headers, body })).status, 403);
    assert.equal((await fetch(base, { method: 'POST', headers: { ...headers, 'x-internal-token': 'wrong' }, body })).status, 403);
    assert.deepEqual(manager.calls, [], 'no child was started for a refused caller');
    const ok = await fetch(base, { method: 'POST', headers: { ...headers, 'x-internal-token': INTERNAL }, body });
    assert.equal(ok.status, 200);
    assert.deepEqual(manager.calls, ['mail']);
    // a gateway without the token configured refuses everyone
    const closed = createApp({ servers: SERVERS, manager, portalJwtSecret: SECRET, internalToken: '', retryAttempts: 1, retryDelayMs: 0 });
    const s2 = await new Promise((r) => { const s = closed.listen(0, '127.0.0.1', () => r(s)); });
    const r2 = await fetch(`http://127.0.0.1:${s2.address().port}/mcp/servers/mail/mcp`,
      { method: 'POST', headers: { ...headers, 'x-internal-token': INTERNAL }, body });
    s2.close();
    assert.equal(r2.status, 403);
  } finally {
    srv.close();
    up.srv.close();
  }
});

/** Run the real mail server as the gateway would: stdio, the token only in its env. */
function runServer(env, messages) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(__dirname, '..', 'servers', 'mail-send.js')],
      { env: { PATH: process.env.PATH, ...env }, stdio: ['pipe', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    const replies = [];
    child.stdout.on('data', (d) => {
      out += d;
      let i;
      while ((i = out.indexOf('\n')) >= 0) {
        const line = out.slice(0, i);
        out = out.slice(i + 1);
        if (line.trim()) replies.push(JSON.parse(line));
        if (replies.length === messages.filter((m) => m.id !== undefined).length) child.stdin.end();
      }
    });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', reject);
    child.on('close', () => resolve({ replies, printed: JSON.stringify(replies) + err }));
    for (const m of messages) child.stdin.write(JSON.stringify(m) + '\n');
  });
}

function parseMime(raw) {
  const boundary = /boundary="([^"]+)"/.exec(raw)[1];
  const parts = raw.split(`--${boundary}`).slice(1, -1);
  return parts.map((p) => {
    const [head, ...rest] = p.replace(/^\r\n/, '').split('\r\n\r\n');
    return { head, body: rest.join('\r\n\r\n') };
  });
}

test('the proof: a real .xlsx goes as a real attachment, byte for byte, from the person\'s own mailbox', async () => {
  const file = fs.readFileSync(XLSX);
  const seen = [];
  const gmail = await listen((req, res) => {
    let raw = '';
    req.on('data', (d) => { raw += d; });
    req.on('end', () => {
      seen.push({ url: req.url, auth: req.headers.authorization, type: req.headers['content-type'], raw });
      res.setHeader('content-type', 'application/json');
      res.end('{"id":"18f0c0ffee","threadId":"18f0c0ffee","labelIds":["SENT"]}');
    });
  });
  const TOKEN = 'ya29.person-token-that-must-not-leak';
  try {
    const { replies, printed } = await runServer({ MAIL_ACCESS_TOKEN: TOKEN, MAIL_API_BASE: `http://127.0.0.1:${gmail.port}` }, [
      { jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 't', version: '1' } } },
      { jsonrpc: '2.0', method: 'notifications/initialized' },
      { jsonrpc: '2.0', id: 2, method: 'tools/list' },
      { jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'send_message', arguments: {
        to: ['cfo@company.example'], cc: ['audit@company.example'], subject: 'กระทบยอดธนาคาร · กันยายน 2569 · ลงตัว',
        text: 'สรุปงานกระทบยอดเดือนกันยายน 2569 แนบไฟล์กระดาษทำการ',
        attachments: [{ filename: 'กระทบยอด_2569-09.xlsx', mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          content_base64: file.toString('base64') }] } } }
    ]);
    assert.deepEqual(replies[1].result.tools.map((t) => t.name), ['send_message']);
    const call = replies[2].result;
    assert.equal(call.isError, false, call.content[0].text);
    assert.deepEqual(JSON.parse(call.content[0].text), { sent: true, id: '18f0c0ffee', attachments: 1 });
    assert.equal(seen.length, 1);
    const got = seen[0];
    assert.equal(got.url, '/upload/gmail/v1/users/me/messages/send?uploadType=media');
    assert.equal(got.auth, `Bearer ${TOKEN}`);
    assert.equal(got.type, 'message/rfc822');
    assert.match(got.raw, /^To: cfo@company\.example\r\nCc: audit@company\.example\r\n/);
    assert.ok(!/^From:/m.test(got.raw), 'no From: Gmail sends from the connected mailbox');
    const subj = /^Subject: =\?UTF-8\?B\?([^?]+)\?=/m.exec(got.raw)[1];
    assert.equal(Buffer.from(subj, 'base64').toString('utf8'), 'กระทบยอดธนาคาร · กันยายน 2569 · ลงตัว');
    const parts = parseMime(got.raw);
    const att = parts.find((p) => /Content-Disposition: attachment/.test(p.head));
    assert.ok(att, 'an attachment part');
    assert.match(att.head, /filename\*=UTF-8''%E0%B8%81/);
    const bytes = Buffer.from(att.body.replace(/\s+/g, ''), 'base64');
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), crypto.createHash('sha256').update(file).digest('hex'));
    assert.equal(bytes.length, file.length);
    assert.ok(!printed.includes(TOKEN), 'the token is never printed');
  } finally {
    gmail.srv.close();
  }
});

test('without a connected mailbox, or refused by the provider, it says so — and never the token', async () => {
  const gmail = await listen((req, res) => { res.statusCode = 403; res.end('{"error":{"message":"Bearer ya29.person-token-x insufficient"}}'); });
  try {
    const call = { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'send_message', arguments: { to: ['a@b.example'], subject: 's', text: 't' } } };
    const none = await runServer({ MAIL_API_BASE: `http://127.0.0.1:${gmail.port}` }, [call]);
    assert.equal(none.replies[0].result.isError, true);
    assert.equal(none.replies[0].result.content[0].text, 'not_connected');
    const refused = await runServer({ MAIL_ACCESS_TOKEN: 'ya29.person-token-x', MAIL_API_BASE: `http://127.0.0.1:${gmail.port}` }, [call]);
    assert.equal(refused.replies[0].result.content[0].text, 'refused: 403');
    assert.ok(!refused.printed.includes('ya29.person-token-x'));
    const bad = await runServer({ MAIL_ACCESS_TOKEN: 'x', MAIL_API_BASE: `http://127.0.0.1:${gmail.port}` },
      [{ ...call, params: { name: 'send_message', arguments: { to: ['not an address'], subject: 's', text: 't' } } }]);
    assert.match(bad.replies[0].result.content[0].text, /^bad_message/);
  } finally {
    gmail.srv.close();
  }
});
