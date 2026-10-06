'use strict';
// A site's robots.txt, read when a page of it is about to be fetched (RFC 9309).
//
// Nothing about a site is decided in advance: the file is fetched from the
// same host at the moment of the fetch, the group that names this fetcher is
// used (else the group for every crawler), and the longest matching rule
// decides; Allow wins a tie. A file that cannot be read because the site is
// failing means "do not fetch"; a file that is not there means there is no rule.

const MAX_BYTES = 512 * 1024;

function parse(text) {
  const groups = [];
  let cur = null;
  let lastWasAgent = false;
  for (const raw of String(text).slice(0, MAX_BYTES).split(/\r\n|\r|\n/)) {
    const line = raw.replace(/#.*$/, '').trim();
    if (!line) continue;
    const at = line.indexOf(':');
    if (at < 0) continue;
    const key = line.slice(0, at).trim().toLowerCase();
    const value = line.slice(at + 1).trim();
    if (key === 'user-agent') {
      if (!cur || !lastWasAgent) {
        cur = { agents: [], rules: [] };
        groups.push(cur);
      }
      cur.agents.push(value.toLowerCase());
      lastWasAgent = true;
      continue;
    }
    lastWasAgent = false;
    if (!cur) continue; // a rule before any group belongs to nobody
    if (key === 'allow' || key === 'disallow') {
      if (value === '') continue; // "Disallow:" with nothing after it forbids nothing
      cur.rules.push({ allow: key === 'allow', pattern: value, line: `${key === 'allow' ? 'Allow' : 'Disallow'}: ${value}` });
    } else if (key === 'crawl-delay') {
      const n = Number(value);
      if (Number.isFinite(n) && n >= 0) cur.delay = n;
    }
  }
  return groups;
}

// The rules that apply to a crawler whose product token is `token`: every
// group naming it, or — when none does — every group for "*".
function rulesFor(groups, token) {
  const t = String(token || '').toLowerCase();
  const named = groups.filter((g) => g.agents.some((a) => a !== '*' && t && (a === t || t.startsWith(a) || a.startsWith(t))));
  const use = named.length ? named : groups.filter((g) => g.agents.includes('*'));
  const rules = [];
  let delay;
  for (const g of use) {
    rules.push(...g.rules);
    if (g.delay !== undefined) delay = g.delay;
  }
  return { rules, group: named.length ? t : use.length ? '*' : '', delay };
}

// A rule's path: a prefix, in which "*" stands for any run of characters and
// a final "$" for the end of the path.
function matches(pattern, path) {
  const anchored = pattern.endsWith('$');
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const source = body.split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*');
  return new RegExp(`^${source}${anchored ? '$' : ''}`, 's').test(path);
}

// decide(text, pathAndQuery, token) → {allowed, rule, group, delay}
function decide(text, pathAndQuery, token) {
  const { rules, group, delay } = rulesFor(parse(text), token);
  let best = null;
  for (const r of rules) {
    if (!matches(r.pattern, pathAndQuery)) continue;
    const len = r.pattern.length;
    if (!best || len > best.len || (len === best.len && r.allow && !best.rule.allow)) best = { len, rule: r };
  }
  if (!best) return { allowed: true, rule: '', group, delay };
  return { allowed: best.rule.allow, rule: best.rule.line, group, delay };
}

module.exports = { parse, decide, MAX_BYTES };
