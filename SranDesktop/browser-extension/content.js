// SRAN Desktop Browser Bridge content script.
// Every numeric ref is bound to one observation and DOM generation.
(() => {
  if (window.__sranBridge) return;
  window.__sranBridge = true;

  const CREDENTIAL_FIELDS =
    /(password|passwd|pwd|pass|secret|token|api[-_ ]?key|apikey|otp|cvv)/i;
  const INTERACTIVE_SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="checkbox"]', '[role="combobox"]',
    '[onclick]', '[contenteditable=""]', '[contenteditable="true"]',
  ].join(',');

  let refs = [];
  let closedShadowHosts = [];
  let observationId = null;
  let observedGeneration = -1;
  let domGeneration = 1;
  const observedRoots = new WeakSet();

  function observeRoot(root) {
    if (!root || observedRoots.has(root)) return;
    observedRoots.add(root);
    new MutationObserver(() => { domGeneration += 1; }).observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: [
        'class', 'hidden', 'disabled', 'aria-disabled', 'aria-expanded',
        'aria-checked', 'aria-selected',
      ],
    });
  }
  observeRoot(document);

  function newObservationId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
      return `dom_${globalThis.crypto.randomUUID().replaceAll('-', '')}`;
    }
    return `dom_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const view = el.ownerDocument && el.ownerDocument.defaultView || window;
    const style = view.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (!el.offsetParent && style.position !== 'fixed') return false;
    return true;
  }

  function labelOf(el) {
    const associatedLabel = el.labels && el.labels.length
      ? Array.from(el.labels).map((label) => label.innerText || label.textContent || '')
        .join(' ')
      : '';
    const text = (el.innerText || el.getAttribute('aria-label') || associatedLabel
      || el.placeholder || el.name || el.value || el.title || '')
      .trim().replace(/\s+/g, ' ');
    return text.length > 80 ? `${text.slice(0, 80)}...` : text;
  }

  function roleOf(el) {
    return (el.getAttribute('role') || ({
      BUTTON: 'button',
      A: 'link',
      INPUT: (el.type === 'checkbox' ? 'checkbox'
        : el.type === 'radio' ? 'radiobutton'
          : el.type === 'file' ? 'fileupload' : 'edit'),
      SELECT: 'combobox',
      TEXTAREA: 'edit',
    })[el.tagName] || el.tagName.toLowerCase()).toLowerCase();
  }

  function stateOf(el) {
    if ('checked' in el) return el.checked ? 'on' : 'off';
    if (el.getAttribute('aria-pressed')) {
      return el.getAttribute('aria-pressed') === 'true' ? 'on' : 'off';
    }
    if (el.getAttribute('aria-selected')) {
      return el.getAttribute('aria-selected') === 'true' ? 'selected' : 'not_selected';
    }
    if ('value' in el && el.type !== 'password') return String(el.value || '');
    return null;
  }

  function describe(index, el, entry) {
    const tag = el.tagName.toLowerCase();
    let line = `[${index}] <${tag}> "${labelOf(el)}" ` +
      `(${el.disabled ? 'disabled' : 'enabled'})`;
    if (entry.framePath !== 'top') line += ` frame="${entry.framePath}"`;
    if (entry.shadowPath) line += ` shadow="${entry.shadowPath}"`;
    if (tag === 'input' || tag === 'textarea') {
      const type = (el.type || '').toLowerCase();
      if (type === 'checkbox' || type === 'radio') line += ` checked=${el.checked}`;
      else if (type !== 'password') {
        line += ` value="${String(el.value || '').slice(0, 60)}"`;
      }
    }
    if (tag === 'select' && el.selectedOptions.length) {
      line += ` value="${labelOf(el.selectedOptions[0])}"`;
    }
    return line;
  }

  function domCandidate(index, entry) {
    const el = entry.weak.deref();
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const parent = el.parentElement;
    const neighbors = parent ? [...parent.children].filter((node) => node !== el)
      .slice(0, 4).map(labelOf).filter(Boolean).join(' | ') : '';
    return {
      ref: index,
      candidate_id: `dom:${observationId}:${index}:${el.id || el.name || labelOf(el)}`,
      role: roleOf(el),
      accessible_name: labelOf(el),
      automation_id: el.id || '',
      bounds: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
      current_state: stateOf(el),
      enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
      visible: isVisible(el),
      text_context: el.title || el.getAttribute('aria-description') || '',
      parent_context: parent ? labelOf(parent) : '',
      neighbor_context: neighbors,
      supported_actions: ['click'],
      observation_id: observationId,
      dom_generation: observedGeneration,
      frame_path: entry.framePath,
      shadow_path: entry.shadowPath,
    };
  }

  // A closed shadow root is unreachable from here by design: `shadowRoot` is
  // null and there is no API to open it. Its host still LOOKS like an empty
  // custom element, so a silent walk would report "nothing there" — the agent
  // would then believe the page has no such control. Count these hosts instead
  // so the observation can state the blind spot honestly.
  function isLikelyClosedShadowHost(node) {
    return node.shadowRoot === null
      && node.tagName.includes('-')
      && node.childElementCount === 0
      && (node.textContent || '').trim() === '';
  }

  function collectRoots(root, framePath, shadowPath, output) {
    observeRoot(root);
    output.push({ root, framePath, shadowPath });
    for (const node of root.querySelectorAll('*')) {
      if (isLikelyClosedShadowHost(node)) {
        closedShadowHosts.push(
          `${framePath}${shadowPath}/${node.id || node.tagName.toLowerCase()}`);
      }
      if (node.shadowRoot) {
        collectRoots(
          node.shadowRoot,
          framePath,
          `${shadowPath}/${node.id || node.tagName.toLowerCase()}`,
          output);
      }
      if (node.tagName === 'IFRAME') {
        try {
          if (node.contentDocument) {
            collectRoots(
              node.contentDocument,
              `${framePath}/${node.id || node.name || 'iframe'}`,
              shadowPath,
              output);
          }
        } catch {
          // Cross-origin frames are not represented by this top-frame walk.
        }
      }
    }
  }

  function walk() {
    refs = [];
    closedShadowHosts = [];
    const lines = [];
    const seen = new Set();
    const roots = [];
    collectRoots(document, 'top', '', roots);
    for (const rootInfo of roots) {
      for (const el of rootInfo.root.querySelectorAll(INTERACTIVE_SELECTOR)) {
        if (seen.has(el) || !isVisible(el)) continue;
        seen.add(el);
        const index = refs.length;
        const entry = {
          weak: new WeakRef(el),
          framePath: rootInfo.framePath,
          shadowPath: rootInfo.shadowPath,
        };
        refs.push(entry);
        lines.push(describe(index, el, entry));
        if (refs.length >= 400) {
          lines.push('(element list capped at 400)');
          break;
        }
      }
      if (refs.length >= 400) break;
    }
    observationId = newObservationId();
    observedGeneration = domGeneration;
    return lines;
  }

  function resolveRef(ref, expectedObservationId, expectedGeneration) {
    if (!expectedObservationId || expectedObservationId !== observationId
        || Number(expectedGeneration) !== observedGeneration
        || domGeneration !== observedGeneration) {
      throw new Error(
        'StaleDomReference: refs are stale; call browser_read_page again');
    }
    const entry = refs[ref];
    const node = entry && entry.weak.deref();
    if (!node || !node.isConnected || !isVisible(node)) {
      throw new Error(
        'StaleDomReference: target was replaced; call browser_read_page again');
    }
    return node;
  }

  function highlight(el) {
    const previous = el.style.outline;
    el.style.outline = '3px solid #7C5CFF';
    setTimeout(() => { el.style.outline = previous; }, 300);
  }

  function realClick(el) {
    el.scrollIntoView({ block: 'center', inline: 'center' });
    el.focus({ preventScroll: true });
    highlight(el);
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const options = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: x,
      clientY: y,
    };
    el.dispatchEvent(new PointerEvent('pointerdown', options));
    el.dispatchEvent(new MouseEvent('mousedown', options));
    el.dispatchEvent(new PointerEvent('pointerup', options));
    el.dispatchEvent(new MouseEvent('mouseup', options));
    return el.dispatchEvent(new MouseEvent('click', options));
  }

  function isCredentialField(el) {
    if ((el.type || '').toLowerCase() === 'password') return true;
    const hints = [
      el.name,
      el.id,
      el.getAttribute('autocomplete'),
      el.placeholder,
      el.getAttribute('aria-label'),
    ].filter(Boolean).join(' ');
    return CREDENTIAL_FIELDS.test(hints);
  }

  function setValue(el, text, clear, submit) {
    if (isCredentialField(el)) {
      throw new Error(
        'This looks like a credential field; the user must type it themselves.');
    }
    el.focus();
    highlight(el);
    if (el.isContentEditable) {
      if (clear !== false) el.textContent = '';
      el.textContent += text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
    } else {
      const proto = el.tagName === 'TEXTAREA'
        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      const next = clear === false ? (el.value || '') + text : text;
      descriptor.set.call(el, next);
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (submit) {
      const keyOptions = {
        bubbles: true,
        cancelable: true,
        key: 'Enter',
        code: 'Enter',
        keyCode: 13,
      };
      el.dispatchEvent(new KeyboardEvent('keydown', keyOptions));
      el.dispatchEvent(new KeyboardEvent('keyup', keyOptions));
      if (el.form && typeof el.form.requestSubmit === 'function') {
        el.form.requestSubmit();
      }
    }
    return el.isContentEditable
      ? String(el.textContent || '')
      : String(el.value || '');
  }

  function pageText() {
    const text = (document.body && document.body.innerText || '')
      .replace(/\n{3,}/g, '\n\n').trim();
    return text.length > 8000
      ? `${text.slice(0, 8000)}\n[page text truncated]`
      : text;
  }

  function fingerprint(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `fnv1a:${(hash >>> 0).toString(16).padStart(8, '0')}`;
  }

  function activeElementFingerprint() {
    const el = document.activeElement;
    if (!el) return '';
    return fingerprint([
      el.tagName,
      el.id || '',
      el.getAttribute('name') || '',
      el.getAttribute('role') || '',
      labelOf(el),
    ].join('|'));
  }

  function interactiveStateFingerprint() {
    const material = [];
    for (const el of document.querySelectorAll(INTERACTIVE_SELECTOR)) {
      if (!isVisible(el)) continue;
      let state = '';
      if (isCredentialField(el)) {
        state = 'sensitive-value-withheld';
      } else if (el instanceof HTMLInputElement
          && (el.type === 'checkbox' || el.type === 'radio')) {
        state = el.checked ? 'checked' : 'unchecked';
      } else if (el instanceof HTMLSelectElement) {
        state = `selected-index:${el.selectedIndex}`;
      } else if ('value' in el) {
        state = String(el.value || '');
      } else if (el.isContentEditable) {
        state = String(el.textContent || '');
      } else {
        state = stateOf(el) || '';
      }
      material.push(fingerprint([
        el.tagName,
        el.id || '',
        el.getAttribute('name') || '',
        state,
      ].join('|')));
      if (material.length >= 400) break;
    }
    return fingerprint(material.join('|'));
  }

  function ownsFocus(el) {
    const root = el.getRootNode();
    if (root instanceof ShadowRoot) return root.activeElement === el;
    return el.ownerDocument.activeElement === el;
  }

  function pageSnapshot(expectedState) {
    const visibleText = pageText();
    const material = [
      location.href,
      document.title,
      document.readyState,
      domGeneration,
      activeElementFingerprint(),
      interactiveStateFingerprint(),
      visibleText,
    ].join('|');
    return {
      frame_path: 'top',
      dom_generation: domGeneration,
      observation_id: observationId,
      url: location.href,
      title: document.title,
      ready_state: document.readyState,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      active_element_fingerprint: activeElementFingerprint(),
      state_fingerprint: fingerprint(material),
      expected_state_observed: expectedState
        ? visibleText.toLowerCase().includes(String(expectedState).toLowerCase())
        : false,
    };
  }

  async function waitForStateChange(
    beforeFingerprint,
    expectedState,
    timeoutMs = 1500) {
    const deadline = performance.now() + timeoutMs;
    let snapshot = pageSnapshot(expectedState);
    while (performance.now() < deadline) {
      if (snapshot.state_fingerprint !== beforeFingerprint
          || snapshot.expected_state_observed) return snapshot;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      snapshot = pageSnapshot(expectedState);
    }
    return snapshot;
  }

  async function handleMessage(msg) {
    switch (msg.cmd) {
      case 'read': {
        const lines = walk();
        let output = `Page: ${document.title} - ${location.href}\n` +
          (lines.length ? lines.join('\n') : '(no interactive elements found)');
        if (closedShadowHosts.length) {
          output += `\n(unobservable: ${closedShadowHosts.length} closed shadow ` +
            `root(s) at ${closedShadowHosts.slice(0, 5).join(', ')}; their ` +
            'contents cannot be read or clicked through the DOM bridge)';
        }
        if (msg.filter === 'full-text') {
          output += `\n\n--- page text ---\n${pageText()}`;
        }
        return {
          text: output,
          closed_shadow_hosts: closedShadowHosts.length,
          ...pageSnapshot(msg.expected_state),
        };
      }
      case 'find': {
        walk();
        const query = String(msg.query || '');
        let matched = [];
        try {
          const nodes = new Set(document.querySelectorAll(query));
          matched = refs.map((entry, index) => ({
            el: entry.weak.deref(),
            entry,
            index,
          })).filter(({ el }) => el && nodes.has(el));
        } catch {
          // Treat invalid selectors as semantic text queries.
        }
        if (matched.length === 0) {
          const needle = query.toLowerCase();
          matched = refs.map((entry, index) => ({
            el: entry.weak.deref(),
            entry,
            index,
          })).filter(({ el }) => el
            && labelOf(el).toLowerCase().includes(needle));
        }
        return {
          text: matched.length
            ? matched.map(({ el, entry, index }) =>
              describe(index, el, entry)).join('\n')
            : `No elements matching "${query}".`,
          ...pageSnapshot(msg.expected_state),
        };
      }
      case 'resolve': {
        walk();
        const hints = (msg.text_hints || [])
          .map((value) => String(value).toLowerCase());
        const role = String(msg.role || '').toLowerCase();
        const candidates = refs.map((entry, index) => ({
          el: entry.weak.deref(),
          entry,
          index,
        }))
          .filter(({ el }) => el && (!role || roleOf(el) === role))
          .filter(({ el }) => hints.length === 0 || hints.some((hint) =>
            [
              labelOf(el),
              el.title || '',
              el.getAttribute('aria-label') || '',
            ].join(' ').toLowerCase().includes(hint)))
          .map(({ entry, index }) => domCandidate(index, entry))
          .filter(Boolean);
        return { candidates, ...pageSnapshot(msg.expected_state) };
      }
      case 'semantic_click': {
        const el = resolveRef(
          msg.ref,
          msg.observation_id,
          msg.dom_generation);
        const before = stateOf(el);
        const beforePage = pageSnapshot(msg.expected_state);
        const candidate = domCandidate(msg.ref, refs[msg.ref]);
        const dispatchAccepted = realClick(el);
        const after = el.isConnected ? stateOf(el) : 'disappeared';
        const settled = await waitForStateChange(
          beforePage.state_fingerprint,
          msg.expected_state);
        return {
          clicked: true,
          candidate_id: candidate.candidate_id,
          before_state: before,
          after_state: after,
          target_activated: dispatchAccepted
            || !el.isConnected
            || ownsFocus(el)
            || before !== after,
          effect_observed: settled.state_fingerprint !== beforePage.state_fingerprint
            || settled.expected_state_observed,
          ...settled,
        };
      }
      case 'type': {
        const el = resolveRef(
          msg.ref,
          msg.observation_id,
          msg.dom_generation);
        const beforePage = pageSnapshot(msg.expected_state);
        const expected = msg.clear === false
          ? String(el.isContentEditable
            ? el.textContent || ''
            : el.value || '') + String(msg.text || '')
          : String(msg.text || '');
        const after = setValue(
          el,
          String(msg.text || ''),
          msg.clear,
          msg.submit);
        const settled = await waitForStateChange(
          beforePage.state_fingerprint,
          msg.expected_state);
        return {
          typed: true,
          after_value: after,
          expected_value: expected,
          target_activated: ownsFocus(el),
          value_verified: after === expected,
          submit_verified: !msg.submit || settled.expected_state_observed,
          effect_observed: settled.state_fingerprint !== beforePage.state_fingerprint
            || settled.expected_state_observed,
          ...settled,
        };
      }
      case 'upload': {
        const el = resolveRef(
          msg.ref,
          msg.observation_id,
          msg.dom_generation);
        if (!(el instanceof HTMLInputElement) || el.type !== 'file') {
          throw new Error('UploadTargetInvalid: target is not a file input.');
        }
        const bytes = Uint8Array.from(
          atob(String(msg.content_base64 || '')),
          (character) => character.charCodeAt(0));
        const file = new File(
          [bytes],
          String(msg.file_name || 'upload.bin'),
          { type: String(msg.content_type || 'application/octet-stream') });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        el.files = transfer.files;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        const selected = el.files && el.files[0];
        return {
          upload_verified: Boolean(selected)
            && selected.name === file.name
            && selected.size === file.size,
          selected_file_name: selected ? selected.name : '',
          selected_file_size: selected ? selected.size : 0,
          target_activated: ownsFocus(el) || document.activeElement === el,
          effect_observed: Boolean(selected),
          ...pageSnapshot(msg.expected_state),
        };
      }
      case 'download_descriptor': {
        const el = resolveRef(
          msg.ref,
          msg.observation_id,
          msg.dom_generation);
        if (!(el instanceof HTMLAnchorElement) || !el.href) {
          throw new Error('DownloadTargetInvalid: target is not a downloadable link.');
        }
        return {
          download_url: el.href,
          file_name: el.download || new URL(el.href).pathname.split('/').pop()
            || 'download.bin',
          ...pageSnapshot(msg.expected_state),
        };
      }
      case 'observe':
        return pageSnapshot(msg.expected_state);
      default:
        throw new Error(`Unknown command '${msg.cmd}'`);
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    Promise.resolve(handleMessage(msg))
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({
        error: String((error && error.message) || error),
      }));
    return true;
  });
})();
