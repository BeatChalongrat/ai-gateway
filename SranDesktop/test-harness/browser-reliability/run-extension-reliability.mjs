import http from 'node:http';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../../apps/web-chat/node_modules/playwright/index.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const extensionPath = path.resolve(here, '../../browser-extension');
const userData = mkdtempSync(path.join(tmpdir(), 'sran-browser-reliability-'));
const contentTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.txt', 'text/plain; charset=utf-8'],
]);

const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  const relative = pathname === '/' || pathname === '/app/details'
    ? 'index.html'
    : pathname.slice(1);
  const candidate = path.resolve(here, relative);
  if (!candidate.startsWith(`${here}${path.sep}`)) {
    response.writeHead(403).end('forbidden');
    return;
  }
  try {
    const body = readFileSync(candidate);
    response.writeHead(200, {
      'content-type': contentTypes.get(path.extname(candidate))
        || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    response.end(body);
  } catch {
    response.writeHead(404).end('not found');
  }
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const address = server.address();
const origin = `http://127.0.0.1:${address.port}`;
const context = await chromium.launchPersistentContext(userData, {
  executablePath: chromium.executablePath(),
  headless: false,
  args: [
    `--disable-extensions-except=${extensionPath}`,
    `--load-extension=${extensionPath}`,
    '--no-first-run',
    '--disable-default-apps',
  ],
});

const metrics = {
  scenarios: 0,
  verified: 0,
  staleReferencesBlocked: 0,
  closedShadowLimitationsReported: 0,
  duplicateSubmits: 0,
  failures: [],
};

try {
  let worker = context.serviceWorkers().find(
    (candidate) => candidate.url().startsWith('chrome-extension://'));
  while (!worker) {
    const candidate = await context.waitForEvent(
      'serviceworker',
      { timeout: 10000 });
    if (candidate.url().startsWith('chrome-extension://')) worker = candidate;
  }
  const call = (method, params = {}) =>
    worker.evaluate(
      async ({ method: bridgeMethod, params: bridgeParams }) =>
        route(bridgeMethod, bridgeParams),
      { method, params });

  const page = context.pages()[0] || await context.newPage();
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  const tabs = await call('tabs.list');
  const fixtureTab = tabs.tabs.find((tab) => tab.url.startsWith(origin));
  if (!fixtureTab) throw new Error('Fixture tab was not visible to the extension.');
  const tabId = fixtureTab.id;

  async function read() {
    return call('page.read', { tab_id: tabId, filter: 'interactive' });
  }

  function refFor(observation, label) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = observation.text.match(
      new RegExp(`\\[(\\d+)\\][^\\n]*"${escaped}"`));
    if (!match) throw new Error(`No ref found for '${label}'.`);
    return Number(match[1]);
  }

  async function click(label, expectedState) {
    metrics.scenarios += 1;
    const observation = await read();
    const result = await call('page.click', {
      tab_id: tabId,
      ref: refFor(observation, label),
      observation_id: observation.observation_id,
      dom_generation: observation.dom_generation,
      expected_state: expectedState,
    });
    if (!result.target_activated || !result.effect_observed) {
      throw new Error(
        `Click '${label}' was not verified: ${JSON.stringify(result)}.`);
    }
    metrics.verified += 1;
    return result;
  }

  metrics.scenarios += 1;
  let observation = await read();
  let result = await call('page.type', {
    tab_id: tabId,
    ref: refFor(observation, 'Workflow value'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
    text: 'phase-three',
    clear: true,
    submit: false,
  });
  if (!result.value_verified || !result.target_activated) {
    throw new Error('Exact field value was not verified.');
  }
  metrics.verified += 1;

  await click('Advance workflow', 'Step two ready');
  await click('Navigate SPA', 'SPA details ready');
  await click('Iframe action', 'Iframe activated');
  await click('Shadow action', 'Shadow activated');
  await click('Open fixture modal', 'Modal opened');
  await click('Close fixture modal', 'Modal closed');
  await click('After scroll target', 'Scrolled target activated');

  metrics.scenarios += 1;
  observation = await read();
  const oldTargetRef = refFor(observation, 'Fresh target');
  result = await call('page.click', {
    tab_id: tabId,
    ref: refFor(observation, 'Replace target node'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
    expected_state: 'Target replaced',
  });
  if (!result.target_activated || !result.effect_observed) {
    throw new Error('Target replacement was not verified.');
  }
  metrics.verified += 1;
  try {
    await call('page.click', {
      tab_id: tabId,
      ref: oldTargetRef,
      observation_id: observation.observation_id,
      dom_generation: observation.dom_generation,
      expected_state: 'Fresh target activated',
    });
    throw new Error('A stale reference was accepted after node replacement.');
  } catch (error) {
    if (!String(error.message || error).includes('StaleDomReference')) throw error;
    metrics.staleReferencesBlocked += 1;
  }
  await click('Fresh target', 'Fresh target activated');

  metrics.scenarios += 1;
  observation = await read();
  result = await call('page.type', {
    tab_id: tabId,
    ref: refFor(observation, 'Workflow value'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
    text: 'submit-once',
    clear: true,
    submit: true,
    expected_state: 'Submitted once',
  });
  const submitCount = await page.locator('#state').getAttribute('data-submit-count');
  if (!result.submit_verified || submitCount !== '1') {
    throw new Error(`Submit verification failed or duplicated: ${submitCount}.`);
  }
  metrics.verified += 1;
  metrics.duplicateSubmits = Math.max(0, Number(submitCount) - 1);

  metrics.scenarios += 1;
  observation = await read();
  result = await call('page.click', {
    tab_id: tabId,
    ref: refFor(observation, 'Open fixture popup'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
    expected_state: '',
  });
  if (!result.effect_observed || result.popup_tabs.length !== 1) {
    throw new Error('Popup tab identity was not verified.');
  }
  metrics.verified += 1;

  metrics.scenarios += 1;
  observation = await read();
  if (observation.closed_shadow_hosts !== 1
      || !observation.text.includes('closed shadow root')) {
    throw new Error(
      `A closed shadow root must be reported as an observable blind spot: ${
        JSON.stringify({
          hosts: observation.closed_shadow_hosts,
          tail: observation.text.slice(-300),
        })}`);
  }
  metrics.closedShadowLimitationsReported += 1;
  metrics.verified += 1;

  metrics.scenarios += 1;
  observation = await read();
  result = await call('page.upload', {
    tab_id: tabId,
    ref: refFor(observation, 'Upload fixture file'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
    file_name: 'upload-fixture.txt',
    content_type: 'text/plain',
    content_base64: Buffer.from('phase31-upload', 'utf8').toString('base64'),
    expected_state: 'Upload selected upload-fixture.txt',
  });
  if (!result.upload_verified || result.selected_file_size !== 14) {
    throw new Error(`Upload selection was not verified: ${JSON.stringify(result)}.`);
  }
  metrics.verified += 1;

  metrics.scenarios += 1;
  observation = await read();
  result = await call('page.download', {
    tab_id: tabId,
    ref: refFor(observation, 'Download fixture file'),
    observation_id: observation.observation_id,
    dom_generation: observation.dom_generation,
  });
  if (!result.download_verified
      || result.file_name !== 'sran-fixture.txt'
      || Buffer.from(result.content_base64, 'base64').toString('utf8')
        !== 'SRAN fixture download') {
    throw new Error(`Download bytes were not verified: ${JSON.stringify(result)}.`);
  }
  metrics.verified += 1;

  console.log(JSON.stringify(metrics));
} catch (error) {
  metrics.failures.push(String(error && error.stack || error));
  console.error(JSON.stringify(metrics));
  process.exitCode = 1;
} finally {
  await context.close();
  await new Promise((resolve) => server.close(resolve));
  rmSync(userData, { recursive: true, force: true });
}
