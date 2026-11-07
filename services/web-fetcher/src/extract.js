'use strict';
// What a page shows, read out of the rendered page.
//
// This function runs inside the page, in a world of its own (the page's
// scripts cannot have replaced the functions it calls). It walks every piece
// of text the document holds and asks the renderer one question about each:
// can a person looking at this page see it? The answer is the renderer's —
// its layout, its styles, what is drawn on top of what — never a reading of
// the words. Text a person can see becomes a numbered block with the box it
// is drawn in; text a person cannot see is listed apart with why; and what a
// page carries outside its text (attributes, comments, metadata, structured
// data) is listed apart again. Only the first list is ever read as the page.
//
// It is kept as one self-contained function: it is sent to the page as text.

/* eslint-disable no-undef */
function extractPage(limits) {
  const MAX_BLOCKS = limits.maxBlocks;
  const MAX_BLOCK_TEXT = limits.maxBlockText;
  const MAX_HIDDEN = limits.maxHidden;
  const doc = document;
  const root = doc.documentElement;
  const body = doc.body || root;
  const sx = window.scrollX;
  const sy = window.scrollY;
  const pageW = Math.max(root.scrollWidth, body.scrollWidth, window.innerWidth);
  const pageH = Math.max(root.scrollHeight, body.scrollHeight, window.innerHeight);
  const viewH = window.innerHeight;
  const NOT_TEXT = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TITLE', 'HEAD', 'META', 'LINK', 'DESC', 'METADATA']);
  const PICTURE = new Set(['IMG', 'VIDEO', 'CANVAS', 'IFRAME', 'PICTURE', 'OBJECT', 'EMBED']);
  const TEXT_ATTRIBUTES = new Set(['alt', 'title', 'placeholder', 'label', 'summary']);

  const styleOf = (() => {
    const seen = new Map();
    return (el) => {
      let s = seen.get(el);
      if (!s) {
        s = getComputedStyle(el);
        seen.set(el, s);
      }
      return s;
    };
  })();

  function rgba(value) {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const p = m[1].split(/[,/ ]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }

  function luminance(c) {
    const f = (v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }

  function parentOf(node) {
    if (node.parentElement) return node.parentElement;
    const r = node.getRootNode && node.getRootNode();
    return r && r.host ? r.host : null;
  }

  // the colour drawn behind an element: the nearest ancestor that paints one
  function backdrop(el) {
    for (let e = el; e; e = parentOf(e)) {
      const s = styleOf(e);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null; // a picture: not known
      const c = rgba(s.backgroundColor);
      if (c && c.a >= 0.5) return c;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  }

  function pathOf(el) {
    const parts = [];
    for (let e = el; e && e !== root; e = parentOf(e)) {
      let n = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) n += 1;
      const inShadow = !e.parentElement && e.getRootNode && e.getRootNode().host;
      parts.push(`${e.tagName.toLowerCase()}:${n}${inShadow ? '^' : ''}`);
    }
    parts.push('html');
    return parts.reverse().join('>');
  }

  function blockOf(el) {
    for (let e = el; e && e !== root; e = parentOf(e)) {
      const d = styleOf(e).display;
      if (d !== 'contents' && !d.startsWith('inline')) return e;
    }
    return body;
  }

  // why a person cannot see this text, or '' when they can
  function unseen(el, rects) {
    if (!rects.length) return 'not_rendered';
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true, opacityProperty: true, contentVisibilityAuto: true })) return 'styled_hidden';
    let opacity = 1;
    for (let e = el; e; e = parentOf(e)) opacity *= Number(styleOf(e).opacity || 1);
    if (opacity < 0.1) return 'transparent';
    const st = styleOf(el);
    if (parseFloat(st.fontSize) < 4) return 'too_small';
    const ink = rgba(st.color);
    if (ink && ink.a < 0.1) return 'transparent';
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const r of rects) {
      left = Math.min(left, r.left); top = Math.min(top, r.top); right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom);
    }
    if (right - left < 2 || bottom - top < 2) return 'no_size';
    if (right + sx <= 0 || bottom + sy <= 0 || left + sx >= pageW || top + sy >= pageH) return 'off_page';
    // cut away by something it sits in
    for (let e = el; e && e !== root; e = parentOf(e)) {
      const s = styleOf(e);
      if (s.overflowX === 'visible' && s.overflowY === 'visible' && (!s.clipPath || s.clipPath === 'none') && (!s.clip || s.clip === 'auto')) continue;
      const b = e.getBoundingClientRect();
      const w = Math.min(right, b.right) - Math.max(left, b.left);
      const h = Math.min(bottom, b.bottom) - Math.max(top, b.top);
      if (w < 2 || h < 2) return 'cut_away';
    }
    const back = backdrop(el);
    if (ink && back) {
      const a = luminance(ink), b = luminance(back);
      const contrast = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (contrast < 1.15) return 'same_colour';
    }
    // drawn over by something else
    const r0 = rects[0];
    const cx = (r0.left + r0.right) / 2;
    const cy = (r0.top + r0.bottom) / 2;
    if (cx >= 0 && cy >= 0 && cx < window.innerWidth && cy < viewH) {
      const hit = doc.elementFromPoint(cx, cy);
      if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) {
        for (let e = hit; e && !e.contains(el); e = parentOf(e)) {
          if (PICTURE.has(e.tagName)) return 'covered';
          const s = styleOf(e);
          const c = rgba(s.backgroundColor);
          if ((c && c.a >= 0.9) || (s.backgroundImage && s.backgroundImage !== 'none')) return 'covered';
        }
      }
    }
    return '';
  }

  function struck(el, stop) {
    for (let e = el; e; e = parentOf(e)) {
      if (String(styleOf(e).textDecorationLine || '').includes('line-through')) return true;
      if (e === stop) break;
    }
    return false;
  }

  const order = [];
  const byBlock = new Map();
  const hidden = [];
  const channels = { title: doc.title || '', meta: [], structured: [], comments: [], attributes: [] };
  const clip = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);
  const squash = (s) => s.replace(/\s+/g, ' ').trim();

  function note(list, value, max) {
    if (list.length < max && value) list.push(value);
  }

  function walk(node) {
    for (let n = node.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 8) {
        note(channels.comments, clip(squash(n.nodeValue || ''), 500), 100);
      } else if (n.nodeType === 3) {
        const raw = n.nodeValue || '';
        if (!raw.trim()) continue;
        const el = parentOf(n);
        if (!el) continue;
        if (NOT_TEXT.has(el.tagName.toUpperCase())) continue;
        const range = doc.createRange();
        range.selectNodeContents(n);
        const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 || r.height > 0);
        const why = unseen(el, rects);
        if (why) {
          if (hidden.length < MAX_HIDDEN) hidden.push({ path: pathOf(el), text: clip(squash(raw), 500), why });
          continue;
        }
        const block = blockOf(el);
        const cut = struck(el, block);
        const key = cut ? `${order.length}:struck` : block;
        let entry = cut ? null : byBlock.get(block);
        if (!entry) {
          entry = { el: block, parts: [], left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity, struck: cut };
          if (!cut) byBlock.set(key, entry);
          order.push(entry);
        }
        entry.parts.push(raw);
        for (const r of rects) {
          entry.left = Math.min(entry.left, r.left + sx); entry.top = Math.min(entry.top, r.top + sy);
          entry.right = Math.max(entry.right, r.right + sx); entry.bottom = Math.max(entry.bottom, r.bottom + sy);
        }
      } else if (n.nodeType === 1) {
        const tag = n.tagName.toUpperCase();
        // words an element carries outside its text: never part of what the page shows
        if (n.attributes) {
          for (const a of Array.from(n.attributes)) {
            const name = a.name.toLowerCase();
            const carries = TEXT_ATTRIBUTES.has(name) || name.startsWith('data-') || name.startsWith('aria-') || (name === 'value' && (tag === 'INPUT' || tag === 'OPTION' || tag === 'DATA'));
            if (carries && a.value && /\p{L}/u.test(a.value)) note(channels.attributes, { attribute: name, text: clip(squash(a.value), 500) }, 600);
          }
        }
        if (tag === 'META') {
          const name = n.getAttribute('name') || n.getAttribute('property') || n.getAttribute('http-equiv') || '';
          note(channels.meta, { name: clip(name, 100), text: clip(squash(n.getAttribute('content') || ''), 500) }, 200);
        } else if (tag === 'SCRIPT') {
          const type = (n.getAttribute('type') || '').toLowerCase();
          if (type.includes('json')) note(channels.structured, clip(squash(n.textContent || ''), 4000), 40);
        } else if (tag === 'TITLE' || tag === 'DESC' || tag === 'METADATA' || tag === 'NOSCRIPT' || tag === 'TEMPLATE') {
          if (n.closest && (n.closest('svg') || tag === 'NOSCRIPT' || tag === 'TEMPLATE')) {
            note(channels.attributes, { attribute: tag.toLowerCase(), text: clip(squash(n.textContent || ''), 500) }, 400);
          }
        }
        if (tag !== 'SCRIPT' && tag !== 'STYLE') walk(n);
        if (n.shadowRoot) walk(n.shadowRoot);
      }
    }
  }
  walk(doc);

  const blocks = [];
  for (const e of order) {
    const text = squash(e.parts.join(' '));
    if (!text) continue;
    if (blocks.length >= MAX_BLOCKS) break;
    blocks.push({
      i: blocks.length,
      path: pathOf(e.el),
      text: clip(text, MAX_BLOCK_TEXT),
      box: [Math.round(e.left), Math.round(e.top), Math.round(e.right - e.left), Math.round(e.bottom - e.top)],
      struck: e.struck,
    });
  }

  // what the page asks of a visitor before it shows anything: a place to type a secret, or a card
  const asks = { password: 0, payment: 0 };
  for (const input of doc.querySelectorAll('input')) {
    const type = (input.getAttribute('type') || '').toLowerCase();
    const auto = (input.getAttribute('autocomplete') || '').toLowerCase();
    const r = input.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (type === 'password' || auto === 'current-password' || auto === 'new-password' || auto === 'one-time-code') asks.password += 1;
    if (auto.startsWith('cc-')) asks.payment += 1;
  }

  const rules = [];
  for (const m of doc.querySelectorAll('meta[name]')) {
    const name = (m.getAttribute('name') || '').toLowerCase();
    if (name === 'robots' || name.endsWith('bot') || name === 'tdm-reservation') rules.push({ name, content: clip(m.getAttribute('content') || '', 300) });
  }

  return {
    blocks,
    hidden,
    channels,
    asks,
    rules,
    page: { width: pageW, height: pageH, lang: root.getAttribute('lang') || '', url: location.href, truncated: order.length > blocks.length },
  };
}

// The page's markup with everything that could run or fetch taken out, as text.
function inertMarkup(maxChars) {
  const copy = document.documentElement.cloneNode(true);
  for (const el of copy.querySelectorAll('script, iframe, frame, frameset, object, embed, applet, base, meta[http-equiv], link[rel~="preload"], link[rel~="modulepreload"], link[rel~="prefetch"], link[rel~="dns-prefetch"], link[rel~="preconnect"], link[rel~="manifest"], link[rel~="serviceworker"]')) el.remove();
  for (const el of copy.querySelectorAll('*')) {
    for (const a of Array.from(el.attributes)) {
      const name = a.name.toLowerCase();
      const value = a.value.trim().toLowerCase();
      if (name.startsWith('on') || name === 'srcdoc' || value.startsWith('javascript:') || value.startsWith('vbscript:') || value.startsWith('data:text/html')) el.removeAttribute(a.name);
    }
  }
  const html = `<!doctype html>\n${copy.outerHTML}`;
  return html.length > maxChars ? { html: html.slice(0, maxChars), truncated: true } : { html, truncated: false };
}

module.exports = { extractPage, inertMarkup };
