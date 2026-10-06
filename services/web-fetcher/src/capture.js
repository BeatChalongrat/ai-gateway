'use strict';
// One fetch: one URL in, one evidence package or one refusal out.
//
// The fetcher holds no credential of any kind and keeps nothing between
// fetches: each one starts a browser with an empty profile, behind a proxy
// that opens only the host the fetch named (proxy.js), and throws both away
// at the end. It never clicks, types, submits or follows a link. What it
// hands back is inert: the page's text as a person would see it, the rest of
// what the page carries listed apart, the markup with nothing left in it
// that could run, a picture of the page, the response's headers and the
// hashes of all of it.
//
// A refusal is a result, not an error: it says which kind of thing stopped
// the fetch (never a guess about the site), and nothing of the page is kept.

const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const tls = require('node:tls');
const dns = require('node:dns');
const { Refusal, parseTarget, resolvePublic } = require('./guard');
const { startProxy, realDial } = require('./proxy');
const robotsTxt = require('./robots');
const { extractPage, inertMarkup } = require('./extract');

const VERSION = '1.0.0';
const PAGE_TYPES = ['text/html', 'application/xhtml+xml'];
const SCREEN_WIDTH = 1280;
const SCREEN_MIN = 900;

const sha256 = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

function defaults(env = process.env) {
  const n = (name, d) => {
    const v = Number(env[name]);
    return Number.isFinite(v) && v > 0 ? v : d;
  };
  const token = (env.WEB_FETCHER_AGENT || 'AgentMarketFetcher').replace(/[^A-Za-z0-9_-]/g, '') || 'AgentMarketFetcher';
  return {
    agentToken: token,
    userAgent: `Mozilla/5.0 (compatible; ${token}/${VERSION}; reads one page when a person asks for it)`,
    own: (env.WEB_FETCHER_OWN_HOSTS || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
    maxBytes: n('WEB_FETCHER_MAX_BYTES', 10 * 1024 * 1024),
    maxDocumentBytes: n('WEB_FETCHER_MAX_DOCUMENT_BYTES', 3 * 1024 * 1024),
    timeoutMs: n('WEB_FETCHER_TIMEOUT_MS', 45000),
    maxScreenHeight: n('WEB_FETCHER_MAX_SCREEN_HEIGHT', 15000),
    limits: { maxBlocks: 4000, maxBlockText: 2000, maxHidden: 400 },
    lookup: dns.promises.lookup,
    dial: realDial,
    launch: null, // () => Promise<Browser>; set by the caller (the service, a test)
  };
}

// ── robots.txt ───────────────────────────────────────────────────────────

function getOnce(scheme, host, ip, port, path, cfg) {
  return new Promise((resolve, reject) => {
    const headers = { host, 'user-agent': cfg.userAgent, accept: 'text/plain,*/*;q=0.1' };
    const socket = cfg.dial(ip, port);
    socket.on('error', reject);
    const opts = { method: 'GET', path, headers, timeout: 8000 };
    const req = scheme === 'https:'
      ? https.request({ ...opts, createConnection: () => tls.connect({ socket, servername: host }) })
      : http.request({ ...opts, createConnection: () => socket });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    req.on('response', (res) => {
      const chunks = [];
      let size = 0;
      res.on('data', (c) => {
        size += c.length;
        if (size <= robotsTxt.MAX_BYTES) chunks.push(c);
        else res.destroy();
      });
      res.on('close', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
      res.on('error', reject);
    });
    req.end();
  });
}

async function readRobots(target, ip, cfg) {
  let scheme = target.url.protocol;
  let path = '/robots.txt';
  const out = { url: `${scheme}//${target.host}/robots.txt`, status: 0, allowed: true, rule: '', group: '', sha256: '' };
  for (let hop = 0; hop < 5; hop += 1) {
    let res;
    try {
      res = await getOnce(scheme, target.host, ip, scheme === 'https:' ? 443 : 80, path, cfg);
    } catch {
      throw new Refusal('robots_unreadable', { robots: out.url });
    }
    out.status = res.status;
    if (res.status >= 300 && res.status < 400 && res.headers.location) {
      let next;
      try {
        next = new URL(res.headers.location, `${scheme}//${target.host}${path}`);
      } catch {
        throw new Refusal('robots_unreadable', { robots: out.url });
      }
      if (next.hostname !== target.host || !['http:', 'https:'].includes(next.protocol)) {
        // the rules live on a host this fetch did not name: they cannot be read, so nothing is fetched
        throw new Refusal('robots_unreadable', { robots: out.url, moved_to: next.hostname });
      }
      scheme = next.protocol;
      path = next.pathname + next.search;
      continue;
    }
    if (res.status >= 500) throw new Refusal('robots_unreadable', { robots: out.url, status: res.status });
    if (res.status >= 400) return out; // no robots.txt: the site states no rule
    const text = res.body.toString('utf8');
    out.sha256 = sha256(res.body);
    const d = robotsTxt.decide(text, target.url.pathname + target.url.search, cfg.agentToken);
    out.allowed = d.allowed;
    out.rule = d.rule;
    out.group = d.group;
    if (d.delay !== undefined) out.crawl_delay = d.delay;
    return out;
  }
  throw new Refusal('robots_unreadable', { robots: out.url });
}

// ── the page ─────────────────────────────────────────────────────────────

function statusRefusal(status) {
  if (status === 401 || status === 407) return new Refusal('credential', { status });
  if (status === 402) return new Refusal('paywall', { status });
  if (status === 403 || status === 429 || status === 451) return new Refusal('site_refused', { status });
  if (status === 404 || status === 410) return new Refusal('not_found', { status });
  if (status >= 400) return new Refusal('site_error', { status });
  if (status === 204 || status === 205) return new Refusal('nothing_shown', { status });
  return null;
}

// a rule the page itself states about automated readers (a header or a meta tag)
function pageSaysNo(rules, headers, token) {
  const said = [];
  const header = headers['x-robots-tag'];
  if (header) said.push({ name: 'x-robots-tag', content: String(header) });
  for (const r of rules || []) said.push(r);
  for (const s of said) {
    const name = s.name.toLowerCase();
    const forUs = name === 'x-robots-tag' || name === 'robots' || name === token.toLowerCase();
    const words = String(s.content).toLowerCase().split(/[,\s:]+/);
    if (forUs && (words.includes('noai') || words.includes('noimageai'))) return { said, refuse: s };
    if (name === 'tdm-reservation' && String(s.content).trim() === '1') return { said, refuse: s };
  }
  return { said, refuse: null };
}

async function inWorld(cdp, frameId, fn, arg) {
  const { executionContextId } = await cdp.send('Page.createIsolatedWorld', { frameId, worldName: `reader-${crypto.randomBytes(4).toString('hex')}` });
  const res = await cdp.send('Runtime.evaluate', {
    expression: `(${fn.toString()})(${JSON.stringify(arg)})`,
    contextId: executionContextId,
    returnByValue: true,
  });
  if (res.exceptionDetails) throw new Error(`page reader: ${res.exceptionDetails.text || 'failed'}`);
  return res.result.value;
}

async function capturePage(target, ip, robots, cfg) {
  const proxy = await startProxy({ host: target.host, ip, maxBytes: cfg.maxBytes, dial: cfg.dial });
  let browser;
  const asked = new Map(); // hosts the page asked for and was not given
  const mainChain = [];
  let leftFor = ''; // where the page tried to send its visitor (not followed)
  let leave;
  const leaving = new Promise((_, reject) => {
    leave = reject;
  });
  leaving.catch(() => {});
  try {
    browser = await cfg.launch({ proxy: { server: `http://127.0.0.1:${proxy.port}`, bypass: '<-loopback>' } });
    const context = await browser.newContext({
      userAgent: cfg.userAgent,
      viewport: { width: SCREEN_WIDTH, height: SCREEN_MIN },
      deviceScaleFactor: 1,
      locale: 'th-TH',
      timezoneId: 'Asia/Bangkok',
      acceptDownloads: false,
      serviceWorkers: 'block',
      permissions: [],
    });
    const page = await context.newPage();
    page.on('dialog', (d) => d.dismiss().catch(() => {}));
    page.on('popup', (p) => p.close().catch(() => {}));
    page.on('request', (req) => {
      if (req.isNavigationRequest() && req.frame() === page.mainFrame()) mainChain.push(req.url());
    });
    await context.route('**/*', (route) => {
      const req = route.request();
      let u;
      try {
        u = new URL(req.url());
      } catch {
        return route.abort('blockedbyclient');
      }
      if (u.protocol === 'data:' || u.protocol === 'blob:' || u.protocol === 'about:') return route.continue();
      if ((u.protocol !== 'http:' && u.protocol !== 'https:') || u.hostname !== target.host) {
        asked.set(u.hostname || u.protocol, (asked.get(u.hostname || u.protocol) || 0) + 1);
        // a page that sends its visitor to another host — by a script, by a meta tag — is not followed:
        // the visitor is answered "nothing there", and the fetch ends as a redirect to that host does
        if (req.isNavigationRequest() && req.frame() === page.mainFrame()) {
          leftFor = leftFor || u.hostname || u.protocol;
          leave(new Refusal('other_host', { to: leftFor, by: 'page' }));
          return route.fulfill({ status: 204, body: '' });
        }
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });

    let response;
    let domReady = false;
    page.once('domcontentloaded', () => {
      domReady = true;
    });
    try {
      response = await Promise.race([
        page.goto(target.url.href, { waitUntil: 'load', timeout: Math.min(30000, cfg.timeoutMs) }),
        leaving,
        // the allowance ran out before the document was there: there is nothing to wait for
        new Promise((_, reject) => {
          proxy.onOverflow(() => {
            if (!domReady) reject(new Refusal('too_large', { limit: cfg.maxBytes }));
          });
        }),
      ]);
    } catch (e) {
      if (e instanceof Refusal) throw e;
      const elsewhere = mainChain.map((u) => new URL(u)).find((u) => u.hostname !== target.host);
      if (elsewhere) throw new Refusal('other_host', { to: elsewhere.hostname });
      if (proxy.stats.overflow && !domReady) throw new Refusal('too_large', { limit: cfg.maxBytes });
      const msg = String(e && e.message);
      if (/download/i.test(msg)) throw new Refusal('not_a_page', { why: 'download' });
      if (/timeout/i.test(msg) && domReady) {
        response = null; // the document arrived; something it loads never finished
      } else if (/timeout/i.test(msg)) {
        throw new Refusal('timeout');
      } else {
        throw new Refusal('unreachable', { error: msg.split('\n')[0].replace(/https?:\/\/\S+/g, '').slice(0, 120) });
      }
    }
    // a redirect that left the named host was not followed: whatever arrived is not that page
    const left = mainChain.map((u) => new URL(u)).find((u) => u.hostname !== target.host);
    const finalUrl = new URL(page.url().startsWith('http') ? page.url() : target.url.href);
    if (finalUrl.hostname !== target.host) throw new Refusal('other_host', { to: finalUrl.hostname });
    if (left && response && response.request().redirectedFrom()) throw new Refusal('other_host', { to: left.hostname });

    let status = 200;
    let headers = {};
    let body = Buffer.alloc(0);
    if (response) {
      status = response.status();
      headers = response.headers();
      const refusal = statusRefusal(status);
      if (refusal) throw refusal;
      const type = String(headers['content-type'] || '').split(';')[0].trim().toLowerCase();
      if (!PAGE_TYPES.includes(type)) throw new Refusal('not_a_page', { content_type: type || 'none' });
      try {
        body = await response.body();
      } catch {
        body = Buffer.alloc(0);
      }
      if (body.length > cfg.maxDocumentBytes) throw new Refusal('too_large', { limit: cfg.maxDocumentBytes, bytes: body.length });
    }
    if (proxy.stats.overflow && !domReady) throw new Refusal('too_large', { limit: cfg.maxBytes });

    await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});

    // the page at its full height, so that what is measured is what the picture shows
    const cdp = await context.newCDPSession(page);
    const { frameTree } = await cdp.send('Page.getFrameTree');
    const frameId = frameTree.frame.id;
    const size = await inWorld(cdp, frameId, () => ({ h: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0) }), null);
    const height = Math.max(SCREEN_MIN, Math.min(cfg.maxScreenHeight, Math.ceil(size.h || SCREEN_MIN)));
    await page.setViewportSize({ width: SCREEN_WIDTH, height });
    await page.waitForLoadState('networkidle', { timeout: 2500 }).catch(() => {});
    await inWorld(cdp, frameId, () => window.scrollTo(0, 0), null);

    if (leftFor) throw new Refusal('other_host', { to: leftFor, by: 'page' });
    const shot = await page.screenshot({ type: 'jpeg', quality: 72, fullPage: false, animations: 'disabled', caret: 'hide' });
    const read = await inWorld(cdp, frameId, extractPage, cfg.limits);
    const markup = await inWorld(cdp, frameId, inertMarkup, 3 * 1024 * 1024);

    const rules = pageSaysNo(read.rules, headers, cfg.agentToken);
    if (rules.refuse) throw new Refusal('page_says_no', { rule: `${rules.refuse.name}: ${rules.refuse.content}`.slice(0, 200) });
    const shown = read.blocks.reduce((n, b) => n + b.text.length, 0);
    const moved = finalUrl.pathname !== target.url.pathname;
    if (read.asks.payment > 0) throw new Refusal('credential', { asks: 'payment' });
    if (read.asks.password > 0 && (moved || shown < 1500)) throw new Refusal('credential', { asks: 'sign_in' });
    if (shown < 80) throw new Refusal('nothing_shown', { asked_elsewhere: [...asked.keys()].slice(0, 20) });

    const files = {
      'page.html': Buffer.from(markup.html, 'utf8'),
      'text.json': Buffer.from(JSON.stringify({ schema: 'web.text.v1', blocks: read.blocks }), 'utf8'),
      'hidden.json': Buffer.from(JSON.stringify({ schema: 'web.hidden.v1', hidden: read.hidden, channels: read.channels }), 'utf8'),
      'screenshot.jpg': shot,
    };
    const listed = {};
    for (const name of Object.keys(files).sort()) listed[name] = { sha256: sha256(files[name]), bytes: files[name].length };
    const capture = {
      outcome: 'captured',
      final_url: finalUrl.href,
      http: {
        status,
        content_type: String(headers['content-type'] || ''),
        content_length: body.length,
        headers: keepHeaders(headers),
      },
      body_sha256: sha256(body),
      snapshot_sha256: sha256(Object.keys(listed).map((n) => `${n}:${listed[n].sha256}\n`).join('')),
      robots,
      page_rules: rules.said,
      hosts: {
        named: target.host,
        address: ip,
        reached: [...proxy.stats.reached],
        not_opened: [...asked.entries()].map(([host, count]) => ({ host, count })).slice(0, 100),
      },
      bytes: { document: body.length, transfer: proxy.stats.bytes, transfer_capped: proxy.stats.overflow },
      page: {
        width: SCREEN_WIDTH, height: read.page.height, screenshot_height: height, lang: read.page.lang,
        markup_truncated: markup.truncated, text_truncated: read.page.truncated,
      },
      counts: {
        shown_blocks: read.blocks.length, shown_characters: shown, struck_blocks: read.blocks.filter((b) => b.struck).length,
        hidden_pieces: read.hidden.length, attributes: read.channels.attributes.length, comments: read.channels.comments.length,
        structured: read.channels.structured.length, meta: read.channels.meta.length,
      },
      files: listed,
    };
    return { capture, files };
  } finally {
    if (browser) await browser.close().catch(() => {});
    await proxy.close();
  }
}

// response headers kept as evidence; anything that could carry a session is left out
function keepHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers)) {
    const name = k.toLowerCase();
    if (name === 'set-cookie' || name === 'authorization' || name === 'proxy-authorization' || name === 'cookie') continue;
    out[name] = String(v).slice(0, 1000);
  }
  return out;
}

// fetchPage never throws for a reason of the page's: it returns {capture, files?}.
async function fetchPage(rawUrl, cfg) {
  const started = new Date();
  const base = { schema: 'web.capture.v1', fetcher_version: VERSION, requested_url: String(rawUrl).slice(0, 2000), started_at: started.toISOString(), clock: 'UTC' };
  const done = (more) => ({ ...base, ...more, finished_at: new Date().toISOString() });
  let timer;
  try {
    const target = parseTarget(rawUrl);
    base.requested_url = target.url.href;
    const work = (async () => {
      const ip = await resolvePublic(target.host, { lookup: cfg.lookup, own: cfg.own });
      const robots = await readRobots(target, ip, cfg);
      if (!robots.allowed) throw new Refusal('robots_disallow', { rule: robots.rule, group: robots.group, robots: robots.url });
      return capturePage(target, ip, robots, cfg);
    })();
    const out = await Promise.race([
      work,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Refusal('timeout')), cfg.timeoutMs);
      }),
    ]);
    work.catch(() => {});
    return { capture: done(out.capture), files: out.files };
  } catch (e) {
    if (e instanceof Refusal) return { capture: done({ outcome: 'refused', reason: e.reason, detail: e.detail }) };
    return { capture: done({ outcome: 'failed', reason: 'fetcher_error', detail: { error: String(e && e.message).split('\n')[0].slice(0, 200) } }) };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { fetchPage, defaults, VERSION, readRobots, pageSaysNo, statusRefusal };
