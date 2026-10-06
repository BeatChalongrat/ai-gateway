'use strict';
// web-fetcher — Agent Market's page fetcher (AM12).
//
//   GET  /health            {"status":"ok","version":…,"agent":…,"enabled":true|false}
//   POST /fetch  {"url":…}  {"capture":{…},"files":{"<name>":"<base64>"}}
//
// One URL per request, one fetch at a time. The service holds no key, no
// token and no volume; it is reached only by agent-runner, on a network with
// no route out, and reaches the web through a second network that nothing
// else is on. The answer is the whole evidence package, or a refusal with
// its reason: nothing is kept here after the answer is sent.

const http = require('node:http');
const { chromium } = require('playwright-core');
const { fetchPage, defaults, VERSION } = require('./capture');

const PORT = Number(process.env.WEB_FETCHER_PORT || 8097);
const MAX_WAITING = Number(process.env.WEB_FETCHER_MAX_WAITING || 4);
// Agent Market's own switch: while it is off this service fetches nothing, whoever asks.
const ENABLED = String(process.env.AGENT_MARKET_ENABLED || '').toLowerCase() === 'true';
const ARGS = [
  '--no-sandbox', // the container is the sandbox: no capability, read-only root, no credential, no volume
  '--disable-dev-shm-usage',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-sync',
  '--disable-default-apps',
  '--disable-extensions',
  '--no-pings',
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,CertificateTransparencyComponentUpdater',
];

const cfg = defaults();
cfg.launch = (opts) => chromium.launch({ headless: true, args: ARGS, ...opts });

let busy = false;
const waiting = [];

function turn() {
  return new Promise((resolve, reject) => {
    if (!busy) {
      busy = true;
      resolve();
    } else if (waiting.length >= MAX_WAITING) {
      reject(new Error('busy'));
    } else {
      waiting.push(resolve);
    }
  });
}

function release() {
  const next = waiting.shift();
  if (next) next();
  else busy = false;
}

function send(res, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': data.length });
  res.end(data);
}

function readJSON(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error('too large'));
        req.destroy();
      } else chunks.push(c);
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    send(res, 200, { status: 'ok', version: VERSION, agent: cfg.agentToken, enabled: ENABLED });
    return;
  }
  if (!ENABLED) {
    send(res, 503, { error: 'disabled' });
    return;
  }
  if (req.method !== 'POST' || req.url !== '/fetch') {
    send(res, 404, { error: 'not found' });
    return;
  }
  let body;
  try {
    body = await readJSON(req, 16 * 1024);
  } catch {
    send(res, 400, { error: 'bad request' });
    return;
  }
  if (!body || typeof body.url !== 'string') {
    send(res, 400, { error: 'url is required' });
    return;
  }
  try {
    await turn();
  } catch {
    send(res, 503, { error: 'busy' });
    return;
  }
  try {
    // a request may ask for less than the service allows, never for more
    const less = (asked, max) => (Number.isFinite(Number(asked)) && Number(asked) > 0 ? Math.min(Number(asked), max) : max);
    const one = { ...cfg, maxBytes: less(body.max_bytes, cfg.maxBytes), timeoutMs: less(body.timeout_ms, cfg.timeoutMs) };
    const out = await fetchPage(body.url, one);
    const files = {};
    for (const [name, data] of Object.entries(out.files || {})) files[name] = data.toString('base64');
    send(res, 200, { capture: out.capture, files });
  } catch (e) {
    send(res, 500, { error: 'fetcher failed' });
  } finally {
    release();
  }
});

server.listen(PORT, '0.0.0.0', () => {
  process.stdout.write(`web-fetcher ${VERSION} listening on ${PORT}\n`);
});
for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => server.close(() => process.exit(0)));
