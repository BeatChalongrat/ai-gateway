import crypto from 'node:crypto';
import http from 'node:http';
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import readline from 'node:readline';
import { chromium } from '../../../apps/web-chat/node_modules/playwright/index.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repository = path.resolve(here, '../..');
const extensionSource = path.join(repository, 'browser-extension');
const nativeHost = path.join(
  repository,
  'src',
  'SranDesktop.BrowserHost',
  'bin',
  'Debug',
  'net8.0-windows10.0.17763.0',
  'SranDesktop.BrowserHost.exe');
const harness = path.join(
  repository,
  'src',
  'SranDesktop.BrowserChainHarness',
  'bin',
  'Debug',
  'net8.0-windows10.0.17763.0',
  'SranDesktop.BrowserChainHarness.exe');
const temporaryRoot = mkdtempSync(path.join(tmpdir(), 'sran-production-chain-'));
const extensionPath = path.join(temporaryRoot, 'extension');
const userData = path.join(temporaryRoot, 'profile');
const manifestPath = path.join(temporaryRoot, 'com.sran.browser.e2e.json');
const databasePath = path.join(temporaryRoot, 'runtime.db');
const workspacePath = path.join(temporaryRoot, 'workspace');
const pipeName = `sran_e2e_${crypto.randomBytes(12).toString('hex')}`;
const pairingToken = crypto.randomBytes(32).toString('base64url');
const registryPath =
  'HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\com.sran.browser.e2e';
const environment = {
  ...process.env,
  SRAN_BROWSER_TEST_MODE: '1',
  SRAN_BROWSER_PIPE_NAME: pipeName,
  SRAN_BROWSER_E2E_TOKEN: pairingToken,
  SRAN_BROWSER_E2E_DB: databasePath,
  SRAN_BROWSER_E2E_WORKSPACE: workspacePath,
};
let context;
let harnessProcess;
let registered = false;

function runRegistry(args) {
  const result = spawnSync('reg.exe', args, {
    env: environment,
    encoding: 'utf8',
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error(
      `reg.exe ${args[0]} failed (${result.status}): ${result.stderr}`);
  }
}

function sha256(file) {
  return crypto.createHash('sha256').update(readFileSync(file)).digest('hex');
}

function createLineReader(process) {
  const lines = [];
  const waiters = [];
  const errors = [];
  readline.createInterface({ input: process.stdout }).on('line', (line) => {
    let value;
    try {
      value = JSON.parse(line);
    } catch {
      errors.push(`Non-JSON harness output: ${line}`);
      return;
    }
    const waiter = waiters.shift();
    if (waiter) waiter.resolve(value);
    else lines.push(value);
  });
  process.stderr.on('data', (chunk) => errors.push(String(chunk)));
  return async function next(timeoutMs = 30000) {
    if (lines.length) return lines.shift();
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject };
      waiters.push(waiter);
      const timer = setTimeout(() => {
        const index = waiters.indexOf(waiter);
        if (index >= 0) waiters.splice(index, 1);
        reject(new Error(
          `Harness response timed out. stderr=${errors.join('').slice(-2000)}`));
      }, timeoutMs);
      waiter.resolve = (value) => {
        clearTimeout(timer);
        resolve(value);
      };
    });
  };
}

async function startHarness() {
  const process = spawn(harness, [], {
    env: environment,
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const nextMessage = createLineReader(process);
  const ready = await nextMessage();
  if (ready.event !== 'ready' || ready.pipe_name !== pipeName) {
    throw new Error(`Harness did not bind the isolated pipe: ${JSON.stringify(ready)}`);
  }
  return { process, nextMessage };
}

async function stopHarness(process) {
  if (!process || process.exitCode !== null) return;
  process.stdin.write(`${JSON.stringify({ command: 'stop' })}\n`);
  process.stdin.end();
  await Promise.race([
    new Promise((resolve) => process.once('exit', resolve)),
    new Promise((_, reject) => setTimeout(
      () => reject(new Error('Harness did not stop within 10 seconds.')),
      10000)),
  ]);
}

async function launchBrowser() {
  return chromium.launchPersistentContext(userData, {
    executablePath: chromium.executablePath(),
    headless: false,
    env: environment,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
      '--no-first-run',
      '--disable-default-apps',
    ],
  });
}

async function extensionWorker(browserContext) {
  let worker = browserContext.serviceWorkers().find(
    (candidate) => candidate.url().startsWith('chrome-extension://'));
  while (!worker) {
    const candidate = await browserContext.waitForEvent(
      'serviceworker',
      { timeout: 10000 });
    if (candidate.url().startsWith('chrome-extension://')) worker = candidate;
  }
  return worker;
}

async function executeChain(process, nextMessage, page, value) {
  process.stdin.write(`${JSON.stringify({ command: 'run', value })}\n`);
  const result = await nextMessage(45000);
  const actualValue = await page.locator('#workflow-value').inputValue();
  const passed =
    result.event === 'result'
    && result.success === true
    && result.verified === true
    && result.task_status === 'Completed'
    && result.agent_step_count >= 2
    && typeof result.bridge_session_id === 'string'
    && actualValue === value
    && result.event_sequence.filter(
      (event) => event === 'ActionDispatching').length >= 2
    && result.event_sequence.filter(
      (event) => event === 'VerificationCompleted').length >= 2
    && result.event_sequence.at(-1) === 'RunCompleted';
  if (!passed) {
    throw new Error(
      `Production chain was not durably verified: ${JSON.stringify({
        result,
        actualValue,
      })}`);
  }
  return result;
}

async function executeTransfers(process, nextMessage, page) {
  process.stdin.write(`${JSON.stringify({ command: 'transfer' })}\n`);
  const result = await nextMessage(60000);
  const selectedName = await page.locator('#safe-upload').evaluate(
    (element) => element.files?.[0]?.name || '');
  const passed =
    result.event === 'transfer_result'
    && result.success === true
    && result.upload_verified === true
    && result.download_verified === true
    && result.task_status === 'Completed'
    && result.agent_step_count >= 2
    && selectedName === 'authorized-upload.txt'
    && result.downloaded_name === 'sran-fixture.txt'
    && result.downloaded_text === 'SRAN fixture download'
    && typeof result.downloaded_sha256 === 'string'
    && result.downloaded_sha256.length === 64
    && result.event_sequence.filter(
      (event) => event === 'VerificationCompleted').length >= 4
    && result.event_sequence.at(-1) === 'RunCompleted';
  if (!passed) {
    throw new Error(
      `Production transfer chain was not durably verified: ${JSON.stringify({
        result,
        selectedName,
      })}`);
  }
  return result;
}

async function executeInterruptedSubmit(process, nextMessage, page) {
  const submitsBefore = Number(
    await page.locator('#state').getAttribute('data-submit-count') || 0);
  process.stdin.write(`${JSON.stringify({
    command: 'interrupt',
    value: `interrupted-${crypto.randomBytes(6).toString('hex')}`,
  })}\n`);
  const result = await nextMessage(60000);
  const submitsAfter = Number(
    await page.locator('#state').getAttribute('data-submit-count') || 0);
  const duplicates = Math.max(0, submitsAfter - submitsBefore - 1);
  const passed =
    result.event === 'interrupt_result'
    && result.relay_killed === true
    && result.dispatched_mutations >= 1
    && result.submit_verified !== true
    && result.submit_reported_success !== true
    && duplicates === 0
    && result.task_status === 'NeedsUser'
    && result.event_sequence.at(-1) === 'RunNeedsUser';
  if (!passed) {
    throw new Error(
      `An interrupted submit was not handled as an unknown outcome: ${JSON.stringify({
        result,
        submitsBefore,
        submitsAfter,
      })}`);
  }
  return { result, duplicates, submitsAfter };
}

async function waitForBridge(process, nextMessage, page, value) {
  // The extension reconnects with backoff after the relay is killed; a plain
  // run only succeeds once a fresh authenticated session exists.
  const deadline = Date.now() + 90000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await executeChain(process, nextMessage, page, value);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  throw new Error(`The bridge never recovered after the relay crash: ${lastError}`);
}

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

try {
  cpSync(extensionSource, extensionPath, { recursive: true });
  mkdirSync(workspacePath, { recursive: true });
  writeFileSync(
    path.join(workspacePath, 'authorized-upload.txt'),
    'SRAN authorized upload fixture',
    'utf8');
  const extensionManifestPath = path.join(extensionPath, 'manifest.json');
  const extensionManifest = JSON.parse(readFileSync(extensionManifestPath, 'utf8'));
  extensionManifest.version_name = 'e2e';
  writeFileSync(
    extensionManifestPath,
    `${JSON.stringify(extensionManifest, null, 2)}\n`,
    'utf8');

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const origin = `http://127.0.0.1:${address.port}`;

  let startedHarness = await startHarness();
  harnessProcess = startedHarness.process;
  let nextHarnessMessage = startedHarness.nextMessage;

  context = await launchBrowser();
  let worker = await extensionWorker(context);
  const extensionId = new URL(worker.url()).host;
  if (!/^[a-p]{32}$/.test(extensionId)) {
    throw new Error(`Chrome returned an invalid extension id: ${extensionId}`);
  }
  await worker.evaluate(
    async (token) => chrome.storage.local.set({ token }),
    pairingToken);

  writeFileSync(
    manifestPath,
    `${JSON.stringify({
      name: 'com.sran.browser.e2e',
      description: 'SRAN isolated production-chain test host',
      path: nativeHost,
      type: 'stdio',
      allowed_origins: [`chrome-extension://${extensionId}/`],
      sran_executable_sha256: sha256(nativeHost),
    }, null, 2)}\n`,
    'utf8');
  runRegistry(['add', registryPath, '/ve', '/t', 'REG_SZ', '/d', manifestPath, '/f']);
  registered = true;

  let page = context.pages()[0] || await context.newPage();
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  const first = await executeChain(
    harnessProcess,
    nextHarnessMessage,
    page,
    `native-chain-${crypto.randomBytes(6).toString('hex')}`);
  const transfers = await executeTransfers(
    harnessProcess,
    nextHarnessMessage,
    page);

  const interrupted = await executeInterruptedSubmit(
    harnessProcess,
    nextHarnessMessage,
    page);
  const afterRelayCrash = await waitForBridge(
    harnessProcess,
    nextHarnessMessage,
    page,
    `relay-crash-recovery-${crypto.randomBytes(6).toString('hex')}`);
  const submitsAfterRecovery = Number(
    await page.locator('#state').getAttribute('data-submit-count') || 0);
  if (submitsAfterRecovery !== interrupted.submitsAfter) {
    throw new Error(
      `Recovery repeated the interrupted submit: ${interrupted.submitsAfter} -> ${
        submitsAfterRecovery}`);
  }

  await context.close();
  context = undefined;
  context = await launchBrowser();
  worker = await extensionWorker(context);
  page = context.pages()[0] || await context.newPage();
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  const afterBrowserRestart = await executeChain(
    harnessProcess,
    nextHarnessMessage,
    page,
    `browser-restart-${crypto.randomBytes(6).toString('hex')}`);
  if (afterBrowserRestart.bridge_session_id === first.bridge_session_id) {
    throw new Error('Chrome restart reused a stale bridge session.');
  }

  await stopHarness(harnessProcess);
  harnessProcess = undefined;
  startedHarness = await startHarness();
  harnessProcess = startedHarness.process;
  nextHarnessMessage = startedHarness.nextMessage;
  const afterDesktopRestart = await executeChain(
    harnessProcess,
    nextHarnessMessage,
    page,
    `desktop-restart-${crypto.randomBytes(6).toString('hex')}`);
  if (afterDesktopRestart.bridge_session_id === afterBrowserRestart.bridge_session_id) {
    throw new Error('Desktop restart reused a stale bridge session.');
  }
  if (new Set([
    first.task_id,
    afterBrowserRestart.task_id,
    afterDesktopRestart.task_id,
  ]).size !== 3) {
    throw new Error('A restarted chain reused a previous AgentRun identity.');
  }

  // Live artifact scan: the pairing token authenticates the whole bridge, so it
  // must never be persisted by the durable runtime that used it.
  const persisted = readFileSync(databasePath);
  const leakedSecrets = [pairingToken, pipeName]
    .filter((secret) => persisted.includes(Buffer.from(secret, 'utf8')));
  if (leakedSecrets.length) {
    throw new Error(
      `Bridge secrets were persisted in the durable store: ${leakedSecrets.length}`);
  }

  console.log(JSON.stringify({
    scenarios: 6,
    verified: 6,
    falseSuccesses: 0,
    duplicateEffects: interrupted.duplicates,
    nativeHostCrashDuringSubmit: {
      relayKilled: interrupted.result.relay_killed,
      reportedSuccess: interrupted.result.submit_reported_success,
      verified: interrupted.result.submit_verified,
      failureCode: interrupted.result.submit_failure_code,
      taskStatus: interrupted.result.task_status,
      duplicateSubmits: interrupted.duplicates,
      recoveredRunVerified: afterRelayCrash.verified,
    },
    chain: [
      'AgentRun',
      'AgentStep',
      'BrowserBridgeService',
      'BrowserHost',
      'ChromiumExtension',
      'DOM',
      'Verification',
      'SQLite',
    ],
    taskStatus: afterDesktopRestart.task_status,
    agentSteps: first.agent_step_count
      + afterBrowserRestart.agent_step_count
      + afterDesktopRestart.agent_step_count,
    durableEvents: afterDesktopRestart.event_sequence,
    persistedBridgeSecrets: leakedSecrets.length,
    sessionBound: true,
    browserRestartReconnected: true,
    desktopRestartReconnected: true,
    staleSessionsReplaced: true,
    uploadVerified: transfers.upload_verified,
    downloadVerified: transfers.download_verified,
    downloadSha256: transfers.downloaded_sha256,
  }));
} catch (error) {
  console.error(String(error && error.stack || error));
  process.exitCode = 1;
} finally {
  if (harnessProcess) await stopHarness(harnessProcess).catch(() => {});
  if (context) await context.close();
  await new Promise((resolve) => server.close(resolve));
  if (registered) {
    spawnSync('reg.exe', ['delete', registryPath, '/f'], {
      env: environment,
      encoding: 'utf8',
      windowsHide: true,
    });
  }
  rmSync(temporaryRoot, { recursive: true, force: true });
}
