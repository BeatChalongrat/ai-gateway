'use strict';
// Which addresses a fetch may be dialled to.
//
// The rule is positive: an address is dialled only when it is ordinary public
// unicast space. Everything the registries set aside — this host, private
// networks, link-local (which is where cloud metadata lives), carrier NAT,
// documentation and benchmark ranges, multicast, reserved space, and every
// IPv6 form that carries an IPv4 address inside it — is refused, each with the
// kind of place it is. Nothing here names a host.

const net = require('node:net');

// [first address, prefix length, kind]
const V4 = [
  ['0.0.0.0', 8, 'this_network'],
  ['10.0.0.0', 8, 'private'],
  ['100.64.0.0', 10, 'carrier_nat'],
  ['127.0.0.0', 8, 'loopback'],
  ['169.254.0.0', 16, 'link_local'],
  ['172.16.0.0', 12, 'private'],
  ['192.0.0.0', 24, 'reserved'],
  ['192.0.2.0', 24, 'documentation'],
  ['192.88.99.0', 24, 'reserved'],
  ['192.168.0.0', 16, 'private'],
  ['198.18.0.0', 15, 'benchmark'],
  ['198.51.100.0', 24, 'documentation'],
  ['203.0.113.0', 24, 'documentation'],
  ['224.0.0.0', 4, 'multicast'],
  ['240.0.0.0', 4, 'reserved'],
];

function v4ToInt(ip) {
  const p = ip.split('.').map(Number);
  return ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3];
}

function v4Kind(ip) {
  const n = v4ToInt(ip);
  for (const [base, bits, kind] of V4) {
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
    if (((n & mask) >>> 0) === ((v4ToInt(base) & mask) >>> 0)) return kind;
  }
  return null;
}

// An IPv6 address as eight 16-bit groups.
function v6Groups(ip) {
  let s = ip.toLowerCase();
  const zone = s.indexOf('%');
  if (zone >= 0) s = s.slice(0, zone);
  let tail = [];
  const lastColon = s.lastIndexOf(':');
  if (s.slice(lastColon + 1).includes('.')) {
    const n = v4ToInt(s.slice(lastColon + 1));
    tail = [(n >>> 16) & 0xffff, n & 0xffff];
    s = s.slice(0, lastColon + 1) + '0:0';
  }
  const [head, rest] = s.split('::');
  const a = head ? head.split(':').filter((x) => x !== '') : [];
  const b = rest !== undefined && rest ? rest.split(':').filter((x) => x !== '') : [];
  let groups;
  if (rest === undefined) groups = a;
  else groups = [...a, ...new Array(Math.max(0, 8 - a.length - b.length)).fill('0'), ...b];
  const out = groups.map((g) => parseInt(g, 16) & 0xffff);
  if (tail.length) {
    out[6] = tail[0];
    out[7] = tail[1];
  }
  return out.length === 8 ? out : null;
}

function embeddedV4(g, at) {
  return `${g[at] >>> 8}.${g[at] & 0xff}.${g[at + 1] >>> 8}.${g[at + 1] & 0xff}`;
}

function v6Kind(ip) {
  const g = v6Groups(ip);
  if (!g) return 'unreadable';
  const zeros = (n) => g.slice(0, n).every((x) => x === 0);
  if (zeros(8)) return 'unspecified';
  if (zeros(7) && g[7] === 1) return 'loopback';
  // an IPv4 address carried inside an IPv6 one is judged as that IPv4 address
  if (zeros(5) && g[5] === 0xffff) return v4Kind(embeddedV4(g, 6)) || null; // ::ffff:a.b.c.d
  if (zeros(6)) return v4Kind(embeddedV4(g, 6)) || 'reserved'; // ::a.b.c.d (deprecated)
  if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) return v4Kind(embeddedV4(g, 6)) || 'nat64';
  if (g[0] === 0x2002) return v4Kind(embeddedV4(g, 1)) || 'six_to_four';
  if (g[0] === 0x2001 && g[1] === 0) return 'teredo';
  if (g[0] === 0x2001 && g[1] === 0xdb8) return 'documentation';
  if (g[0] === 0x100 && g[1] === 0 && g[2] === 0 && g[3] === 0) return 'discard';
  if ((g[0] & 0xfe00) === 0xfc00) return 'private';
  if ((g[0] & 0xffc0) === 0xfe80) return 'link_local';
  if ((g[0] & 0xffc0) === 0xfec0) return 'private';
  if ((g[0] & 0xff00) === 0xff00) return 'multicast';
  // global unicast is 2000::/3; anything else is not assigned for it
  if ((g[0] & 0xe000) !== 0x2000) return 'reserved';
  return null;
}

// notPublic(ip) is why an address may not be dialled, or null when it is
// public unicast space.
function notPublic(ip) {
  const v = net.isIP(ip);
  if (v === 4) return v4Kind(ip);
  if (v === 6) return v6Kind(ip);
  return 'unreadable';
}

module.exports = { notPublic };
