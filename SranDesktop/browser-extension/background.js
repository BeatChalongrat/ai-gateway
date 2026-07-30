// SRAN Desktop Browser Bridge MV3 service worker.
const HOST = chrome.runtime.getManifest().version_name === 'e2e'
  ? 'com.sran.browser.e2e'
  : 'com.sran.browser';
const PROTOCOL_VERSION = 2;
const LOAD_TIMEOUT_MS = 20000;
const ACTION_SETTLE_TIMEOUT_MS = 3000;
const browserInstanceId = `chrome_${crypto.randomUUID().replaceAll('-', '')}`;
const extensionSessionId = `extension_${crypto.randomUUID().replaceAll('-', '')}`;
const MUTATING_METHODS = new Set([
  'tabs.activate', 'tabs.new', 'tabs.close',
  'page.navigate', 'page.back', 'page.forward', 'page.reload',
  'page.click', 'page.type', 'page.select', 'page.check',
  'page.scroll', 'page.upload', 'page.download',
]);

let port = null;
let backoffMs = 1000;
let browserProfileId = '';
let bridgeSessionId = '';
let heartbeatTimer = null;
let reconnectTimer = null;
const navigationIds = new Map();
const cancelledRequests = new Set();

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId === 0) {
    navigationIds.set(
      details.tabId,
      (navigationIds.get(details.tabId) || 0) + 1);
  }
});
chrome.tabs.onRemoved.addListener((tabId) => navigationIds.delete(tabId));

async function getToken() {
  const stored = await chrome.storage.local.get('token');
  return stored.token || '';
}

async function getOrCreateProfileId() {
  const stored = await chrome.storage.local.get('browser_profile_id');
  if (typeof stored.browser_profile_id === 'string'
      && /^profile_[a-z0-9]{16,64}$/.test(stored.browser_profile_id)) {
    return stored.browser_profile_id;
  }
  const created = `profile_${crypto.randomUUID().replaceAll('-', '')}`;
  await chrome.storage.local.set({ browser_profile_id: created });
  return created;
}

async function setStatus(connected) {
  await chrome.storage.local.set({
    status: {
      connected,
      paired: connected,
      bridge_session_id: connected ? bridgeSessionId : '',
      at: Date.now(),
    },
  });
}

function connect() {
  if (port) return;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  bridgeSessionId = '';
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  setStatus(false);
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch {
    port = null;
    scheduleReconnect();
    return;
  }
  const connectedPort = port;
  connectedPort.onMessage.addListener(
    (message) => onNativeMessage(connectedPort, message));
  connectedPort.onDisconnect.addListener(() => {
    if (port !== connectedPort) return;
    port = null;
    bridgeSessionId = '';
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    setStatus(false);
    scheduleReconnect();
  });
  Promise.all([getToken(), getOrCreateProfileId()]).then(([token, profileId]) => {
    browserProfileId = profileId;
    if (port === connectedPort) {
      connectedPort.postMessage({
        token,
        hello: true,
        protocol_version: PROTOCOL_VERSION,
        extension_session_id: extensionSessionId,
        browser_instance_id: browserInstanceId,
        browser_profile_id: browserProfileId,
      });
    }
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, backoffMs);
  backoffMs = Math.min(backoffMs * 2, 30000);
}

async function onNativeMessage(sourcePort, message) {
  if (port !== sourcePort) return;
  if (message && message.control === 'hello_ack') {
    if (message.protocol_version !== PROTOCOL_VERSION
        || message.extension_session_id !== extensionSessionId
        || typeof message.bridge_session_id !== 'string'
        || !message.bridge_session_id.startsWith('bridge_')) {
      sourcePort.disconnect();
      return;
    }
    bridgeSessionId = message.bridge_session_id;
    backoffMs = 1000;
    await setStatus(true);
    clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(sendHeartbeat, 10000);
    return;
  }
  if (message && message.control === 'cancel'
      && message.bridge_session_id === bridgeSessionId
      && Number.isSafeInteger(message.request_id)) {
    cancelledRequests.add(message.request_id);
    return;
  }
  return onRequest(sourcePort, message);
}

async function sendHeartbeat() {
  if (!port || !bridgeSessionId) return;
  const token = await getToken();
  port.postMessage({
    token,
    heartbeat: true,
    bridge_session_id: bridgeSessionId,
    extension_session_id: extensionSessionId,
  });
}

async function onRequest(sourcePort, message) {
  if (!message || typeof message.id === 'undefined') return;
  const token = await getToken();
  const context = message.context || {};
  const requestBridgeSessionId = bridgeSessionId;
  try {
    if (!bridgeSessionId || message.bridge_session_id !== bridgeSessionId) {
      throw new Error('StaleBrowserSession: request did not match the active bridge session.');
    }
    if (MUTATING_METHODS.has(message.method)
        && (typeof context.run_id !== 'string'
            || typeof context.step_id !== 'string'
            || !context.run_id
            || !context.step_id)) {
      throw new Error(
        'RuntimeContextRequired: mutable browser action has no AgentRun/AgentStep.');
    }
    if (cancelledRequests.delete(message.id)) {
      throw new Error('ActionCancelledBeforeDispatch');
    }
    const result = await route(message.method, message.params || {});
    if (port !== sourcePort || bridgeSessionId !== requestBridgeSessionId) return;
    sourcePort.postMessage({
      token,
      id: message.id,
      bridge_session_id: bridgeSessionId,
      run_id: context.run_id ?? null,
      step_id: context.step_id ?? null,
      result,
    });
  } catch (error) {
    if (port !== sourcePort || bridgeSessionId !== requestBridgeSessionId) return;
    sourcePort.postMessage({
      token,
      id: message.id,
      bridge_session_id: bridgeSessionId,
      run_id: context.run_id ?? null,
      step_id: context.step_id ?? null,
      error: String((error && error.message) || error),
    });
  } finally {
    cancelledRequests.delete(message.id);
  }
}

async function route(method, params) {
  switch (method) {
    case 'tabs.list': {
      const tabs = await chrome.tabs.query({});
      return {
        browser_instance_id: browserInstanceId,
        browser_profile_id: browserProfileId,
        extension_session_id: extensionSessionId,
        bridge_session_id: bridgeSessionId,
        tabs: tabs.map((tab) => ({
          id: tab.id,
          title: tab.title || '',
          url: tab.url || '',
          active: tab.active,
          navigation_id: navigationIds.get(tab.id) || 0,
          status: tab.status || 'unknown',
        })),
      };
    }
    case 'tabs.activate': {
      const tab = await chrome.tabs.update(params.tab_id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      const verified = await chrome.tabs.get(params.tab_id);
      return {
        ok: verified.active === true,
        tab_id: verified.id,
        active: verified.active === true,
        window_id: verified.windowId,
      };
    }
    case 'tabs.new': {
      const tab = await chrome.tabs.create({ url: params.url });
      const observation = await waitForLoad(tab.id);
      return { created: true, ...observation };
    }
    case 'tabs.close': {
      await chrome.tabs.remove(params.tab_id);
      let closed = false;
      try {
        await chrome.tabs.get(params.tab_id);
      } catch {
        closed = true;
      }
      return { ok: closed, closed, tab_id: params.tab_id };
    }
    case 'page.navigate': {
      const tabId = await resolveTab(params.tab_id);
      const before = await chrome.tabs.get(tabId);
      if (before.url !== params.url) {
        await chrome.tabs.update(tabId, { url: params.url });
      }
      const observation = await waitForLoad(tabId);
      return {
        ...observation,
        requested_url: params.url,
        navigation_verified: observation.ready_state === 'complete'
          && Boolean(observation.url),
      };
    }
    case 'page.read': {
      const tabId = await resolveTab(params.tab_id);
      const result = await contentCall(tabId, {
        cmd: 'read',
        filter: params.filter || 'interactive',
        expected_state: params.expected_state || '',
      });
      return decorateObservation(tabId, result);
    }
    case 'page.find': {
      const tabId = await resolveTab(params.tab_id);
      const result = await contentCall(tabId, {
        cmd: 'find',
        query: params.query,
      });
      return decorateObservation(tabId, result);
    }
    case 'page.resolve': {
      const tabId = await resolveTab(params.tab_id);
      const result = await contentCall(tabId, {
        cmd: 'resolve',
        text_hints: params.text_hints || [],
        role: params.role || '',
      });
      return decorateObservation(tabId, result);
    }
    case 'page.observe': {
      const tabId = await resolveTab(params.tab_id);
      const result = await contentCall(tabId, {
        cmd: 'observe',
        expected_state: params.expected_state || '',
      });
      return decorateObservation(tabId, result);
    }
    case 'page.click': {
      const tabId = await resolveTab(params.tab_id);
      const tabsBefore = new Set(
        (await chrome.tabs.query({})).map((tab) => tab.id));
      const before = await observeTab(tabId, params.expected_state);
      const result = await contentCall(tabId, {
        cmd: 'semantic_click',
        ref: params.ref,
        observation_id: params.observation_id,
        dom_generation: params.dom_generation,
        expected_state: params.expected_state || '',
      });
      const settled = await waitForActionSettlement(
        tabId,
        before,
        params.expected_state);
      const popupTabs = (await chrome.tabs.query({}))
        .filter((tab) => !tabsBefore.has(tab.id))
        .map((tab) => ({
          tab_id: tab.id,
          window_id: tab.windowId,
          url: tab.url || '',
          title: tab.title || '',
        }));
      return {
        ...result,
        ...settled,
        target_activated: result.target_activated === true
          || popupTabs.length > 0,
        popup_tabs: popupTabs,
        navigated: settled.navigation_id !== before.navigation_id
          || settled.url !== before.url,
        effect_observed: result.effect_observed === true
          || popupTabs.length > 0
          || settled.state_fingerprint !== before.state_fingerprint
          || settled.navigation_id !== before.navigation_id,
      };
    }
    case 'page.type': {
      const tabId = await resolveTab(params.tab_id);
      const before = await observeTab(tabId, params.expected_state);
      const result = await contentCall(tabId, {
        cmd: 'type',
        ref: params.ref,
        observation_id: params.observation_id,
        dom_generation: params.dom_generation,
        text: params.text,
        clear: params.clear,
        submit: params.submit,
        expected_state: params.expected_state || '',
      });
      const settled = params.submit
        ? await waitForActionSettlement(tabId, before, params.expected_state)
        : await observeTab(tabId, params.expected_state);
      return {
        ...result,
        ...settled,
        submit_verified: !params.submit
          || settled.expected_state_observed === true,
      };
    }
    case 'page.upload': {
      const tabId = await resolveTab(params.tab_id);
      const result = await contentCall(tabId, {
        cmd: 'upload',
        ref: params.ref,
        observation_id: params.observation_id,
        dom_generation: params.dom_generation,
        file_name: params.file_name,
        content_type: params.content_type,
        content_base64: params.content_base64,
        expected_state: params.expected_state || '',
      });
      return decorateObservation(tabId, result);
    }
    case 'page.download': {
      const tabId = await resolveTab(params.tab_id);
      const descriptor = await contentCall(tabId, {
        cmd: 'download_descriptor',
        ref: params.ref,
        observation_id: params.observation_id,
        dom_generation: params.dom_generation,
        expected_state: params.expected_state || '',
      });
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
      let response;
      try {
        response = await fetch(descriptor.download_url, {
          credentials: 'include',
          redirect: 'follow',
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!response.ok) {
        throw new Error(`DownloadFailed: HTTP ${response.status}.`);
      }
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.byteLength > 512 * 1024) {
        throw new Error('DownloadTooLarge: maximum bridge transfer is 512 KiB.');
      }
      const digest = new Uint8Array(
        await crypto.subtle.digest('SHA-256', bytes));
      return decorateObservation(tabId, {
        download_verified: true,
        file_name: descriptor.file_name,
        content_type: response.headers.get('content-type')
          || 'application/octet-stream',
        content_base64: bytesToBase64(bytes),
        byte_count: bytes.byteLength,
        sha256: Array.from(digest)
          .map((value) => value.toString(16).padStart(2, '0'))
          .join(''),
        state_fingerprint: descriptor.state_fingerprint,
        dom_generation: descriptor.dom_generation,
        observation_id: descriptor.observation_id,
        ready_state: descriptor.ready_state,
      });
    }
    case 'page.screenshot': {
      const tabId = await resolveTab(params.tab_id);
      const tab = await chrome.tabs.get(tabId);
      if (!tab.active) {
        await chrome.tabs.update(tabId, { active: true });
        await chrome.windows.update(tab.windowId, { focused: true });
        const active = await chrome.tabs.get(tabId);
        if (!active.active) throw new Error('Tab activation could not be verified.');
      }
      const image = await chrome.tabs.captureVisibleTab(
        tab.windowId,
        { format: 'jpeg', quality: 55 });
      const observation = await observeTab(tabId, '');
      return { image, ...observation };
    }
    default:
      throw new Error(`Unknown method '${method}'`);
  }
}

async function resolveTab(tabId) {
  if (typeof tabId === 'number') return tabId;
  const [active] = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true,
  });
  if (!active) throw new Error('No active tab.');
  return active.id;
}

async function contentCall(tabId, message) {
  await ensureContentScript(tabId);
  const response = await chrome.tabs.sendMessage(tabId, message);
  if (response && response.error) throw new Error(response.error);
  return response || {};
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js'],
    });
  } catch (error) {
    throw new Error(
      'Cannot access this page (protected Chrome pages are off-limits): ' +
      String((error && error.message) || error));
  }
}

async function decorateObservation(tabId, contentObservation) {
  const tab = await chrome.tabs.get(tabId);
  const zoom = await chrome.tabs.getZoom(tabId);
  return {
    ...contentObservation,
    browser_instance_id: browserInstanceId,
    browser_profile_id: browserProfileId,
    extension_session_id: extensionSessionId,
    bridge_session_id: bridgeSessionId,
    tab_id: tabId,
    navigation_id: navigationIds.get(tabId) || 0,
    url: contentObservation.url || tab.url || '',
    title: contentObservation.title || tab.title || '',
    ready_state: contentObservation.ready_state
      || (tab.status === 'complete' ? 'complete' : tab.status || 'unknown'),
    zoom,
  };
}

async function observeTab(tabId, expectedState) {
  const result = await contentCall(tabId, {
    cmd: 'observe',
    expected_state: expectedState || '',
  });
  return decorateObservation(tabId, result);
}

async function waitForActionSettlement(tabId, before, expectedState) {
  const deadline = Date.now() + ACTION_SETTLE_TIMEOUT_MS;
  let current = before;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'loading') return waitForLoad(tabId);
    current = await observeTab(tabId, expectedState);
    if (current.navigation_id !== before.navigation_id
        || current.state_fingerprint !== before.state_fingerprint
        || current.expected_state_observed === true) {
      return current;
    }
    await delay(50);
  }
  return current;
}

async function waitForLoad(tabId) {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  let previousFingerprint = null;
  let stableObservations = 0;
  let latest = null;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'complete') {
      try {
        latest = await observeTab(tabId, '');
        if (latest.ready_state === 'complete') {
          if (latest.state_fingerprint === previousFingerprint) {
            stableObservations += 1;
          } else {
            stableObservations = 0;
          }
          previousFingerprint = latest.state_fingerprint;
          if (stableObservations >= 1) return latest;
        }
      } catch {
        // The content world may not exist yet; continue bounded polling.
      }
    }
    await delay(50);
  }
  throw new Error(`Navigation did not reach a stable complete state in ${LOAD_TIMEOUT_MS} ms.`);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function bytesToBase64(bytes) {
  let binary = '';
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

chrome.runtime.onStartup.addListener(connect);
connect();
