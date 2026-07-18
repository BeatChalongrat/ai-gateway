// Options page: stores the pairing token and mirrors the connection status the
// service worker writes to chrome.storage.local.
const tokenInput = document.getElementById('token');
const statusText = document.getElementById('statusText');
const dot = document.querySelector('.dot');

async function refresh() {
  const { token, status } = await chrome.storage.local.get(['token', 'status']);
  if (typeof token === 'string') tokenInput.value = token;
  const connected = !!(status && status.connected);
  dot.className = 'dot ' + (connected ? 'on' : 'off');
  statusText.textContent = connected
    ? 'Connected to SRAN Desktop'
    : 'Not connected — is SRAN Desktop running?';
}

document.getElementById('save').addEventListener('click', async () => {
  await chrome.storage.local.set({ token: tokenInput.value.trim() });
  // Nudge the worker to reconnect with the new token by reloading the extension runtime.
  chrome.runtime.reload();
});

chrome.storage.onChanged.addListener(refresh);
refresh();
