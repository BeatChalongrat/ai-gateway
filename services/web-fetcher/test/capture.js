'use strict';
// Captures pages of the stand-in web with the real fetcher and writes each snapshot's files to a folder:
// the fixtures other suites read (the procurement package's bundled sample, the injection corpus).
//
//   docker run --rm -v <repo>/services/web-fetcher/test:/opt/web-fetcher/test:ro -v <out>:/out \
//     ai-gateway-web-fetcher:dev node test/capture.js /out s1=http://host/path s2=…
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { chromium } = require('playwright-core');
const { fetchPage, defaults } = require('../src/capture');
const standin = require('./sites/server');

const PUBLIC = '93.184.215.14';

(async () => {
  const [out, ...pairs] = process.argv.slice(2);
  const server = await standin.start(0, '127.0.0.1');
  const port = server.address().port;
  const cfg = defaults({});
  cfg.lookup = async (host) => {
    if (standin.HOSTS.includes(host) || host.endsWith('.other-host.test')) return [{ address: PUBLIC, family: 4 }];
    throw new Error('ENOTFOUND');
  };
  cfg.dial = () => net.connect(port, '127.0.0.1');
  cfg.launch = (opts) => chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'], ...opts });
  let bad = 0;
  for (const pair of pairs) {
    const at = pair.indexOf('=');
    const id = pair.slice(0, at);
    const got = await fetchPage(pair.slice(at + 1), cfg);
    fs.writeFileSync(path.join(out, `capture_${id}.json`), JSON.stringify(got.capture, null, 1));
    if (got.capture.outcome !== 'captured') {
      bad += 1;
      process.stdout.write(`${id}: ${got.capture.outcome} ${got.capture.reason}\n`);
      continue;
    }
    fs.writeFileSync(path.join(out, `text_${id}.json`), got.files['text.json']);
    fs.writeFileSync(path.join(out, `hidden_${id}.json`), got.files['hidden.json']);
    fs.writeFileSync(path.join(out, `shot_${id}.jpg`), got.files['screenshot.jpg']);
    process.stdout.write(`${id}: captured ${got.capture.counts.shown_blocks} blocks, ${got.files['screenshot.jpg'].length} bytes of picture\n`);
  }
  server.close();
  process.exit(bad ? 1 : 0);
})();
