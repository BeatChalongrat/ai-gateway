'use strict';
// What a fetch may be opened to, decided before anything is dialled.
//
// A fetch names one URL. Its host is the only host that fetch reaches, and it
// is reached only at an address that is public. Refusals are kinds of
// destination, never names of sites: a scheme that is not the web's, a port
// that is not the web's, an address that is not public, a host that is this
// deployment's own.

const dns = require('node:dns');
const net = require('node:net');
const { notPublic } = require('./addr');

class Refusal extends Error {
  constructor(reason, detail) {
    super(reason);
    this.reason = reason;
    this.detail = detail || {};
  }
}

const PORTS = { 'http:': 80, 'https:': 443 };

function parseTarget(raw) {
  let u;
  try {
    u = new URL(String(raw));
  } catch {
    throw new Refusal('bad_url');
  }
  if (!(u.protocol in PORTS)) throw new Refusal('scheme', { scheme: u.protocol.replace(':', '') });
  if (u.username || u.password) throw new Refusal('credential_in_url');
  if (u.port && Number(u.port) !== PORTS[u.protocol]) throw new Refusal('port', { port: Number(u.port) });
  // WHATWG URL has already lower-cased the host and converted an IDN to punycode
  let host = u.hostname;
  if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1);
  if (!host) throw new Refusal('bad_url');
  u.hash = '';
  return { url: u, host, port: PORTS[u.protocol] };
}

function ownHost(host, own) {
  const h = host.replace(/\.$/, '');
  return own.some((o) => o && (h === o || h.endsWith(`.${o}`)));
}

// resolvePublic turns the named host into the one address the whole fetch is
// dialled to. Every address the name has must be public: a name with one
// public and one private address is refused, so a second lookup can never be
// answered differently from the first.
async function resolvePublic(host, { lookup = dns.promises.lookup, own = [] } = {}) {
  if (ownHost(host, own)) throw new Refusal('own_service');
  if (net.isIP(host)) {
    const why = notPublic(host);
    if (why) throw new Refusal('not_public', { kind: why });
    return host;
  }
  if (!host.replace(/\.$/, '').includes('.')) {
    // a bare name ("backend", "localhost") is a name of this network, not of the web
    throw new Refusal('not_public', { kind: 'local_name' });
  }
  let found;
  try {
    found = await lookup(host, { all: true, verbatim: true });
  } catch {
    throw new Refusal('unresolvable');
  }
  if (!found || found.length === 0) throw new Refusal('unresolvable');
  for (const a of found) {
    const why = notPublic(a.address);
    if (why) throw new Refusal('not_public', { kind: why });
  }
  const v4 = found.find((a) => a.family === 4);
  return (v4 || found[0]).address;
}

module.exports = { Refusal, parseTarget, resolvePublic, ownHost, PORTS };
