// AM10 — what is on a screen and not in the language the person chose. One reader for the chat app's
// walk and the admin app's: it opens nothing and judges nothing beyond the letters — text nodes and the
// four attributes a person reads (placeholder, title, aria-label, alt) of what is shown now.
//
//   leaks(page, "th" | "en", { notApps, within, only, keep, tag }) → [{ text, where }]
//
//   notApps  a selector for what is on screen without being the app's own words (what people typed,
//            names of their things, a model's answer); anything inside it is left out
//   within   a selector: read only inside that element's parent (a row's own confirmation)
//   only     a selector: read only text inside such elements (an admin table's headings and controls,
//            not the data in its cells)
//   keep     the source of a regex of Latin words a Thai screen keeps on purpose (names, formats, keys)
//   tag      text carrying it was typed by the walk itself and is left out
//
// On a Thai screen a string is reported when it has two Latin words that are not kept names, or is one
// such word alone with no Thai beside it; on an English screen, when it has any Thai letter.
"use strict";

async function leaks(page, locale, { notApps = "", within = null, only = null, keep = "^$", tag = "\u0000" } = {}) {
  return page.evaluate(({ locale, keep, notApps, tag, within, only }) => {
    const root = (within && document.querySelector(within)?.parentElement) || document.body;
    const KEEP = new RegExp(keep);
    const skip = (el) => (notApps ? !!el.closest(notApps) : false) || (only ? !el.closest(only) : false);
    const shown = (el) => (el.checkVisibility ? el.checkVisibility({ checkVisibilityCSS: true }) : getComputedStyle(el).display !== "none");
    const wrong = (text) => {
      if (!text || text.includes(tag)) return false;
      if (/^[\w.+-]+@[\w.-]+$/.test(text)) return false; // an address
      if (locale === "en") return /[ก-๙]/.test(text);
      const words = (text.match(/[A-Za-z][A-Za-z'’-]{2,}/g) || []).filter((w) => !KEEP.test(w));
      return words.length >= 2 || (words.length === 1 && !/[ก-๙]/.test(text) && text.length < 40);
    };
    const out = new Map();
    const add = (raw, where) => {
      const text = String(raw).replace(/\s+/g, " ").trim().slice(0, 110);
      if (wrong(text) && !out.has(text)) out.set(text, where);
    };
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || el.closest("script, style, noscript, textarea, input") || skip(el) || !shown(el)) continue;
      add(n.textContent, "text");
    }
    for (const el of root.querySelectorAll("[placeholder], [title], [aria-label], img[alt]")) {
      if ((notApps && el.closest(notApps)) || !shown(el)) continue;
      for (const a of ["placeholder", "title", "aria-label", "alt"]) {
        const v = el.getAttribute(a);
        if (v) add(v, a);
      }
    }
    return [...out].map(([text, where]) => ({ text, where }));
  }, { locale, keep, notApps, tag, within, only });
}

/** Each string once, with every surface it was read on. */
function flat(bySurface) {
  const by = new Map();
  for (const [surface, list] of Object.entries(bySurface)) {
    for (const { text, where } of list) {
      const e = by.get(text) || { text, where, on: [] };
      e.on.push(surface);
      by.set(text, e);
    }
  }
  return [...by.values()].sort((m, n) => m.text.localeCompare(n.text));
}

module.exports = { leaks, flat };
