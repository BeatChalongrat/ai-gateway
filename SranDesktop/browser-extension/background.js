// SRAN Desktop Browser Bridge — MV3 service worker.
// Owns the native-messaging port to SranDesktop.BrowserHost.exe (which relays
// to the desktop app's named pipe). Wire: app→here {id, method, params};
// here→app {token, id, result|error} (+ one {token, hello:true} after connect).
// The pairing token is entered once on the options page.

const HOST = 'com.sran.browser';
const LOAD_TIMEOUT_MS = 20000;
const SETTLE_MS = 800; // network-quiet fallback after readyState complete (no webRequest permission)

let port = null;
let backoffMs = 1000;
let tokenRejected = false; // this connection died on a pairing-token mismatch

async function getToken() {
  const stored = await chrome.storage.local.get('token');
  return stored.token || '';
}

async function setStatus(connected, reason) {
  await chrome.storage.local.set({ status: { connected, reason: reason || null, at: Date.now() } });
}

function connect() {
  if (port) return; // already connected (or hello in flight)
  tokenRejected = false;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  port.onMessage.addListener(onMessage);
  port.onDisconnect.addListener(() => {
    port = null;
    // Keep the actionable "token mismatch" status the reject just wrote —
    // a plain "not connected" would overwrite the one hint the user needs.
    if (!tokenRejected) setStatus(false);
    scheduleReconnect();
  });
  getToken().then((token) => {
    if (port) port.postMessage({ token, hello: true });
    // Status stays as-is until the app acks the hello (no optimistic green).
  });
}

function scheduleReconnect() {
  setTimeout(connect, backoffMs);
  backoffMs = Math.min(backoffMs * 2, 30000);
}

// Belt-and-braces reconnect: the backoff setTimeout dies with the MV3 service
// worker (~30s idle), so a Chrome left open before the desktop app starts
// would otherwise stay grey until a manual poke. Alarms survive worker death —
// every minute, reconnect if we aren't connected. An established native port
// keeps the worker alive by itself, so the alarm is a no-op while healthy.
chrome.alarms.create('sran-reconnect', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'sran-reconnect' && !port) connect();
});

async function onMessage(msg) {
  if (!msg) return;
  // Control frames from the app (FIX-B): honest connection status.
  if (msg.hello_ack) {
    backoffMs = 1000;
    await setStatus(true);
    return;
  }
  if (msg.hello_reject) {
    tokenRejected = true;
    await setStatus(false, 'token_mismatch');
    return; // the app drops the connection right after this
  }
  if (typeof msg.id === 'undefined') return;
  const token = await getToken();
  try {
    const result = await route(msg.method, msg.params || {});
    port?.postMessage({ token, id: msg.id, result });
  } catch (e) {
    port?.postMessage({ token, id: msg.id, error: String((e && e.message) || e) });
  }
}

async function route(method, p) {
  switch (method) {
    case 'tabs.list': {
      const tabs = await chrome.tabs.query({});
      return { tabs: tabs.map((t) => ({ id: t.id, title: t.title || '', url: t.url || '', active: t.active })) };
    }
    case 'tabs.activate': {
      const tab = await chrome.tabs.update(p.tab_id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      return { ok: true };
    }
    case 'tabs.new': {
      const tab = await chrome.tabs.create({ url: p.url });
      const done = await waitForLoad(tab.id);
      return { tab_id: tab.id, url: done.url, title: done.title };
    }
    case 'tabs.close': {
      await chrome.tabs.remove(p.tab_id);
      return { ok: true };
    }
    case 'page.navigate': {
      const tabId = await resolveTab(p.tab_id);
      await chrome.tabs.update(tabId, { url: p.url });
      const done = await waitForLoad(tabId);
      // tab_id in the result: the model needs the REAL id for follow-up
      // read/click calls (it used to invent one — Chrome ids aren't 1,2,3).
      return { tab_id: tabId, url: done.url, title: done.title };
    }
    case 'page.read':
      return contentCall(p.tab_id, { cmd: 'read', filter: p.filter || 'interactive' });
    case 'page.find':
      return contentCall(p.tab_id, { cmd: 'find', query: p.query });
    case 'page.click': {
      const tabId = await resolveTab(p.tab_id);
      const res = await contentCall(tabId, { cmd: 'click', ref: p.ref }, tabId);
      // The click may start a navigation — give it a beat, then wait it out.
      await sleep(600);
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === 'loading') {
        const done = await waitForLoad(tabId);
        return { ...res, navigated: true, url: done.url, title: done.title };
      }
      return { ...res, navigated: false };
    }
    case 'page.type':
      return contentCall(p.tab_id, { cmd: 'type', ref: p.ref, text: p.text, clear: p.clear, submit: p.submit });
    case 'page.screenshot': {
      const tabId = await resolveTab(p.tab_id);
      const tab = await chrome.tabs.get(tabId);
      if (!tab.active) {
        await chrome.tabs.update(tabId, { active: true });
        await chrome.windows.update(tab.windowId, { focused: true });
        await sleep(300);
      }
      // JPEG: keeps the frame under the 1 MB native-messaging limit
      // (BrowserWire.MaxMessageBytes on the C# side).
      const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 55 });
      return { image, title: tab.title || '' };
    }
    default:
      throw new Error(`Unknown method '${method}'`);
  }
}

async function resolveTab(tabId) {
  if (typeof tabId === 'number') {
    // Trust-but-verify: a model sometimes invents ids (1, 2, ...). A number
    // that names no real tab falls back to the active tab instead of erroring
    // the whole call ("No tab with id: 1").
    try {
      await chrome.tabs.get(tabId);
      return tabId;
    } catch (e) {
      // not a real tab — fall through to the active one
    }
  }
  const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!active) throw new Error('No active tab.');
  return active.id;
}

async function contentCall(tabId, message) {
  const id = await resolveTab(tabId);
  await ensureContentScript(id);
  const response = await chrome.tabs.sendMessage(id, message);
  if (response && response.error) throw new Error(response.error);
  return response || {};
}

async function ensureContentScript(tabId) {
  try {
    // content.js is idempotent (window.__sranBridge guard) — inject freely.
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  } catch (e) {
    throw new Error('Cannot access this page (chrome:// pages, the Web Store and some ' +
      'protected pages are off-limits to extensions): ' + ((e && e.message) || e));
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// document.readyState complete (via tabs.onUpdated) + a settle window.
async function waitForLoad(tabId) {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  await new Promise((resolve) => {
    const listener = (updatedId, info) => {
      if (updatedId === tabId && info.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    // Already complete (or never fires): poll as a safety net.
    const poll = setInterval(async () => {
      try {
        const tab = await chrome.tabs.get(tabId);
        if (tab.status === 'complete' || Date.now() > deadline) {
          clearInterval(poll);
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      } catch {
        clearInterval(poll);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }, 250);
  });
  await sleep(SETTLE_MS);
  const tab = await chrome.tabs.get(tabId);
  return { url: tab.url || '', title: tab.title || '' };
}

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect); // first install + extension update/reload
connect();
