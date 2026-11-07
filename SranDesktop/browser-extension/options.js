// Options page: stores an OPTIONAL pasted pairing token and mirrors the
// connection status the service worker writes to chrome.storage.local.
// Normally nothing is pasted — the desktop app writes pairing.json into this
// folder and background.js reads it (FIX70: the hint used to send people to a
// Settings pane that shows no token).
const tokenInput = document.getElementById('token');
const statusText = document.getElementById('statusText');
const dot = document.querySelector('.dot');
const ownId = document.getElementById('ownId');

async function refresh() {
  if (ownId) ownId.textContent = chrome.runtime.id;
  const { token, status } = await chrome.storage.local.get(['token', 'status']);
  if (typeof token === 'string') tokenInput.value = token;
  const connected = !!(status && status.connected);
  dot.className = 'dot ' + (connected ? 'on' : 'off');
  const reason = status && status.reason;
  if (connected) {
    statusText.textContent = 'เชื่อมต่อกับ SRAN Desktop แล้ว (Connected)';
  } else if (reason === 'token_mismatch') {
    statusText.textContent =
      'โทเคนไม่ตรง — ในแอป SRAN Desktop → Settings → Browser กด "สร้างโทเคนจับคู่ใหม่" แล้วรอสักครู่ หรือถ้าวางโทเคนไว้ที่นี่ ให้ลบออกแล้วกด Save '
      + '(Pairing token mismatch — in SRAN Desktop → Settings → Browser press "Generate a new pairing token" and wait, or clear a pasted token here and save)';
  } else if (reason === 'host_forbidden') {
    statusText.textContent =
      'Chrome ปฏิเสธ: ID ของส่วนขยายนี้ (' + chrome.runtime.id + ') ไม่ตรงกับ "Expected id" ใน SRAN Desktop → Settings → Browser — ถ้าโหลดจากโฟลเดอร์อื่น กด "ฉันโหลดโฟลเดอร์อื่น…" ในแอป '
      + '(Chrome refused: this extension\'s id is not the one SRAN Desktop authorised — in SRAN Desktop → Settings → Browser make "Expected id" match, or use "I loaded a different folder…")';
  } else if (reason === 'host_not_found') {
    statusText.textContent =
      'ยังไม่มีการลงทะเบียน native host — เปิด SRAN Desktop หนึ่งครั้ง (แอปลงทะเบียนให้เองตอนเริ่ม) '
      + '(Native host not registered — start SRAN Desktop once; it registers itself on launch)';
  } else {
    statusText.textContent = 'ยังไม่เชื่อมต่อ — SRAN Desktop เปิดอยู่หรือไม่? (Not connected — is SRAN Desktop running?)';
  }
}

document.getElementById('save').addEventListener('click', async () => {
  const pasted = tokenInput.value.trim();
  // An empty field means "use the token the app provided" — drop any stale paste.
  if (pasted) await chrome.storage.local.set({ token: pasted });
  else await chrome.storage.local.remove('token');
  // Nudge the worker to reconnect with the new token by reloading the extension runtime.
  chrome.runtime.reload();
});

chrome.storage.onChanged.addListener(refresh);
refresh();
