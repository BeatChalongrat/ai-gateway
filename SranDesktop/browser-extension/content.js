// SRAN Desktop Browser Bridge — content script (injected on demand).
// Walks the DOM for interactive elements, hands out stable numeric refs, and
// performs clicks/typing with real event dispatch. Idempotent: executeScript
// may run it repeatedly.
(() => {
  if (window.__sranBridge) return;
  window.__sranBridge = true;

  // Field-name deny list — parallel to the C# CredentialTextGuard label list
  // (SranDesktop.Application/Services/CredentialTextGuard.cs); update both.
  const SRAN_CREDENTIAL_FIELDS =
    /(password|passwd|pwd|pass|secret|token|api[-_ ]?key|apikey|otp|cvv)/i;

  const INTERACTIVE_SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="checkbox"]', '[role="combobox"]',
    '[onclick]', '[contenteditable=""]', '[contenteditable="true"]',
  ].join(',');

  // ref → WeakRef(node): refs stay valid across small page mutations without
  // pinning removed nodes in memory; a full re-read rebuilds the table.
  let refs = [];

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (!el.offsetParent && style.position !== 'fixed') return false;
    return true;
  }

  function labelOf(el) {
    const text = (el.innerText || el.getAttribute('aria-label') || el.placeholder
      || el.name || el.value || el.title || '').trim().replace(/\s+/g, ' ');
    return text.length > 80 ? text.slice(0, 80) + '…' : text;
  }

  function describe(index, el) {
    const tag = el.tagName.toLowerCase();
    let line = `[${index}] <${tag}> "${labelOf(el)}" (${el.disabled ? 'disabled' : 'enabled'})`;
    if (tag === 'input' || tag === 'textarea') {
      const type = (el.type || '').toLowerCase();
      if (type === 'checkbox' || type === 'radio') line += ` checked=${el.checked}`;
      else if (type !== 'password') line += ` value="${String(el.value || '').slice(0, 60)}"`;
    }
    if (tag === 'select' && el.selectedOptions.length) {
      line += ` value="${labelOf(el.selectedOptions[0])}"`;
    }
    return line;
  }

  function walk() {
    refs = [];
    const lines = [];
    const seen = new Set();
    for (const el of document.querySelectorAll(INTERACTIVE_SELECTOR)) {
      if (seen.has(el) || !isVisible(el)) continue;
      seen.add(el);
      const index = refs.length;
      refs.push(new WeakRef(el));
      lines.push(describe(index, el));
      if (refs.length >= 400) {
        lines.push('(element list capped at 400)');
        break;
      }
    }
    return lines;
  }

  function resolveRef(ref) {
    const weak = refs[ref];
    const node = weak && weak.deref();
    if (!node || !node.isConnected || !isVisible(node)) {
      throw new Error('refs are stale — call browser_read_page again');
    }
    return node;
  }

  function highlight(el) {
    const prev = el.style.outline;
    el.style.outline = '3px solid #7C5CFF';
    setTimeout(() => { el.style.outline = prev; }, 300);
  }

  function realClick(el) {
    el.scrollIntoView({ block: 'center', inline: 'center' });
    highlight(el);
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', opts));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
  }

  function isCredentialField(el) {
    if ((el.type || '').toLowerCase() === 'password') return true;
    const hints = [el.name, el.id, el.getAttribute('autocomplete'), el.placeholder,
      el.getAttribute('aria-label')].filter(Boolean).join(' ');
    return SRAN_CREDENTIAL_FIELDS.test(hints);
  }

  function setValue(el, text, clear, submit) {
    if (isCredentialField(el)) {
      throw new Error('This looks like a credential field — the user must type it themselves.');
    }
    el.focus();
    highlight(el);
    if (el.isContentEditable) {
      if (clear !== false) el.textContent = '';
      el.textContent += text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
    } else {
      // React-safe: go through the native value setter so framework state
      // trackers see the change, then announce it.
      const proto = el.tagName === 'TEXTAREA'
        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      const next = clear === false ? (el.value || '') + text : text;
      setter.call(el, next);
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (submit) {
      const keyOpts = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13 };
      el.dispatchEvent(new KeyboardEvent('keydown', keyOpts));
      el.dispatchEvent(new KeyboardEvent('keyup', keyOpts));
      if (el.form && typeof el.form.requestSubmit === 'function') el.form.requestSubmit();
    }
  }

  function pageText() {
    const text = (document.body && document.body.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
    return text.length > 8000 ? text.slice(0, 8000) + '\n[page text truncated]' : text;
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    try {
      switch (msg.cmd) {
        case 'read': {
          const lines = walk();
          let out = `Page: ${document.title} — ${location.href}\n` +
            (lines.length ? lines.join('\n') : '(no interactive elements found)');
          if (msg.filter === 'full-text') out += '\n\n--- page text ---\n' + pageText();
          sendResponse({ text: out });
          break;
        }
        case 'find': {
          walk();
          const q = String(msg.query || '');
          let matched = [];
          try {
            // A valid CSS selector wins; otherwise fall through to text match.
            const nodes = new Set(document.querySelectorAll(q));
            matched = refs.map((w, i) => ({ el: w.deref(), i }))
              .filter(({ el }) => el && nodes.has(el));
          } catch { /* not a selector */ }
          if (matched.length === 0) {
            const needle = q.toLowerCase();
            matched = refs.map((w, i) => ({ el: w.deref(), i }))
              .filter(({ el }) => el && labelOf(el).toLowerCase().includes(needle));
          }
          sendResponse({
            text: matched.length
              ? matched.map(({ el, i }) => describe(i, el)).join('\n')
              : `No elements matching "${q}".`,
          });
          break;
        }
        case 'click':
          realClick(resolveRef(msg.ref));
          sendResponse({ clicked: true });
          break;
        case 'type':
          setValue(resolveRef(msg.ref), String(msg.text || ''), msg.clear, msg.submit);
          sendResponse({ typed: true });
          break;
        default:
          sendResponse({ error: `Unknown command '${msg.cmd}'` });
      }
    } catch (e) {
      sendResponse({ error: String((e && e.message) || e) });
    }
    return true; // keep the message channel open for sendResponse
  });
})();
