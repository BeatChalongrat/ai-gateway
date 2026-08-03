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
  if (connected) {
    statusText.textContent = 'เชื่อมต่อกับ SRAN Desktop แล้ว (Connected)';
  } else if (status && status.reason === 'token_mismatch') {
    statusText.textContent =
      'โทเคนไม่ตรง — คัดลอกโทเคนใหม่จาก SRAN Desktop → Settings → เบราว์เซอร์ แล้ววางที่นี่ '
      + '(Pairing token mismatch — paste a fresh token from SRAN Desktop → Settings → Browser)';
  } else {
    statusText.textContent = 'ยังไม่เชื่อมต่อ — SRAN Desktop เปิดอยู่หรือไม่? (Not connected — is SRAN Desktop running?)';
  }
}

document.getElementById('save').addEventListener('click', async () => {
  await chrome.storage.local.set({ token: tokenInput.value.trim() });
  // Nudge the worker to reconnect with the new token by reloading the extension runtime.
  chrome.runtime.reload();
});

chrome.storage.onChanged.addListener(refresh);
refresh();
