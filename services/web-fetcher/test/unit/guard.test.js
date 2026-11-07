'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const { Refusal, parseTarget, resolvePublic } = require('../../src/guard');
const { startProxy } = require('../../src/proxy');

function refusal(fn) {
  try {
    fn();
  } catch (e) {
    assert.ok(e instanceof Refusal, String(e));
    return e;
  }
  assert.fail('not refused');
  return null;
}

async function refusalOf(promise) {
  try {
    await promise;
  } catch (e) {
    assert.ok(e instanceof Refusal, String(e));
    return e;
  }
  assert.fail('not refused');
  return null;
}

const names = (table) => async (host) => {
  if (!table[host]) throw new Error('ENOTFOUND');
  return table[host].map((address) => ({ address, family: address.includes(':') ? 6 : 4 }));
};

test('only the web\'s own schemes and ports are a target', () => {
  assert.equal(parseTarget('https://Shop.Example.com/p/1?x=1#frag').url.href, 'https://shop.example.com/p/1?x=1');
  assert.equal(parseTarget('http://shop.example.com:80/').port, 80);
  for (const u of ['file:///etc/passwd', 'ftp://shop.example.com/a', 'gopher://x.example.com/', 'javascript:alert(1)', 'data:text/html,hi', 'chrome://settings']) {
    assert.equal(refusal(() => parseTarget(u)).reason, 'scheme', u);
  }
  assert.equal(refusal(() => parseTarget('https://shop.example.com:8443/')).reason, 'port');
  assert.equal(refusal(() => parseTarget('http://shop.example.com:6379/')).reason, 'port');
  assert.equal(refusal(() => parseTarget('https://user:secret@shop.example.com/')).reason, 'credential_in_url');
  assert.equal(refusal(() => parseTarget('not a url')).reason, 'bad_url');
});

test('an address written in the URL is judged like any other', async () => {
  const cases = ['http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data/', 'http://10.1.2.3/', 'http://192.168.65.254/',
    'http://[::1]/', 'http://[fd00:ec2::254]/', 'http://[::ffff:169.254.169.254]/', 'http://2130706433/', 'http://0x7f.1/', 'http://0/'];
  for (const u of cases) {
    const t = parseTarget(u);
    const e = await refusalOf(resolvePublic(t.host, { lookup: names({}) }));
    assert.equal(e.reason, 'not_public', u);
  }
  assert.equal(await resolvePublic('93.184.215.14', { lookup: names({}) }), '93.184.215.14');
});

test('a name of this network, and a name that resolves to it, are refused', async () => {
  for (const host of ['localhost', 'backend', 'gateway', 'postgres']) {
    assert.equal((await refusalOf(resolvePublic(host, { lookup: names({ [host]: ['93.184.215.14'] }) }))).detail.kind, 'local_name', host);
  }
  const lookup = names({
    'inside.example.com': ['10.0.0.8'], 'metadata.example.com': ['169.254.169.254'], 'shop.example.com': ['93.184.215.14'],
    'two-faced.example.com': ['93.184.215.14', '127.0.0.1'], 'six.example.com': ['::1'],
  });
  assert.equal((await refusalOf(resolvePublic('inside.example.com', { lookup }))).detail.kind, 'private');
  assert.equal((await refusalOf(resolvePublic('metadata.example.com', { lookup }))).detail.kind, 'link_local');
  assert.equal((await refusalOf(resolvePublic('six.example.com', { lookup }))).detail.kind, 'loopback');
  // one public address among private ones is not enough: every address the name has must be public
  assert.equal((await refusalOf(resolvePublic('two-faced.example.com', { lookup }))).detail.kind, 'loopback');
  assert.equal((await refusalOf(resolvePublic('nowhere.example.com', { lookup }))).reason, 'unresolvable');
  assert.equal(await resolvePublic('shop.example.com', { lookup }), '93.184.215.14');
});

test('this deployment\'s own names are refused whatever they resolve to', async () => {
  const lookup = names({ 'chat.company.example': ['93.184.215.14'], 'api.chat.company.example': ['93.184.215.14'], 'notchat.company.example': ['93.184.215.14'] });
  const own = ['chat.company.example'];
  assert.equal((await refusalOf(resolvePublic('chat.company.example', { lookup, own }))).reason, 'own_service');
  assert.equal((await refusalOf(resolvePublic('api.chat.company.example', { lookup, own }))).reason, 'own_service');
  assert.equal(await resolvePublic('notchat.company.example', { lookup, own }), '93.184.215.14');
});

// ── the proxy: the one door ──────────────────────────────────────────────

function origin(handler) {
  return new Promise((resolve) => {
    const s = http.createServer(handler);
    s.listen(0, '127.0.0.1', () => resolve(s));
  });
}

function viaProxy(port, url) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, method: 'GET', path: url, headers: { host: new URL(url).host } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
      res.on('error', reject);
    });
    req.on('error', reject);
    req.end();
  });
}

function connectVia(port, hostPort) {
  return new Promise((resolve, reject) => {
    const s = net.connect(port, '127.0.0.1', () => s.write(`CONNECT ${hostPort} HTTP/1.1\r\nHost: ${hostPort}\r\n\r\n`));
    s.once('data', (d) => {
      resolve(String(d).split('\r\n')[0]);
      s.destroy();
    });
    s.on('error', reject);
  });
}

test('the proxy opens the named host at the address it was given, and nothing else', async () => {
  const shop = await origin((req, res) => res.end(`shop saw host ${req.headers.host}`));
  const dialled = [];
  // the fetch was resolved to a public address; the test's network stands in for it
  const dial = (ip, port) => {
    dialled.push(`${ip}:${port}`);
    return net.connect(shop.address().port, '127.0.0.1');
  };
  const proxy = await startProxy({ host: 'shop.example.com', ip: '93.184.215.14', dial });
  try {
    const ok = await viaProxy(proxy.port, 'http://shop.example.com/p/1');
    assert.deepEqual([ok.status, ok.body], [200, 'shop saw host shop.example.com']);
    assert.deepEqual(dialled, ['93.184.215.14:80']);

    for (const u of ['http://other.example.com/', 'http://www.shop.example.com/', 'http://127.0.0.1/', 'http://169.254.169.254/latest/', 'http://shop.example.com:8080/']) {
      assert.equal((await viaProxy(proxy.port, u)).status, 403, u);
    }
    assert.equal(await connectVia(proxy.port, 'shop.example.com:443'), 'HTTP/1.1 200 Connection Established');
    for (const hp of ['evil.example.com:443', '169.254.169.254:80', '10.0.0.1:443', 'shop.example.com:22', 'shop.example.com:6379', '[::1]:443']) {
      assert.equal(await connectVia(proxy.port, hp), 'HTTP/1.1 403 Forbidden', hp);
    }
    // nothing but the named host was ever dialled
    assert.deepEqual([...new Set(dialled)].sort(), ['93.184.215.14:443', '93.184.215.14:80']);
    assert.deepEqual([...proxy.stats.reached], ['shop.example.com']);
    assert.ok(proxy.stats.refused.get('evil.example.com') === 1 && proxy.stats.refused.get('169.254.169.254') === 2);
  } finally {
    await proxy.close();
    shop.close();
  }
});

test('the proxy closes the door when the fetch\'s allowance is spent', async () => {
  const big = await origin((req, res) => {
    res.writeHead(200, { 'content-type': 'text/html' });
    const chunk = Buffer.alloc(64 * 1024, 'a');
    let n = 0;
    const more = () => {
      while (n < 64 && res.write(chunk)) n += 1;
      if (n >= 64) res.end();
      else res.once('drain', more);
    };
    more();
  });
  const proxy = await startProxy({ host: 'shop.example.com', ip: '93.184.215.14', maxBytes: 256 * 1024, dial: () => net.connect(big.address().port, '127.0.0.1') });
  try {
    await viaProxy(proxy.port, 'http://shop.example.com/huge').catch(() => null);
    assert.equal(proxy.stats.overflow, true);
    assert.ok(proxy.stats.bytes < 1024 * 1024, `stopped after ${proxy.stats.bytes} bytes`);
    assert.equal((await viaProxy(proxy.port, 'http://shop.example.com/again').catch(() => ({ status: 0 }))).status === 200, false);
  } finally {
    await proxy.close();
    big.close();
  }
});
