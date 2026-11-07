// FIX69 — where a USER-TYPED remote MCP URL may point (host side).
//
// The same two rules the sidecar enforces at connect time
// (SranDesktop.Infrastructure/Mcp/Remote/RemoteTargetPolicy.cs), applied here
// first so the user gets the reason before anything is dialled:
//   • https only — a key must never travel in clear, and the sidecar's own
//     rule for this path is the same;
//   • internal targets (loopback, RFC 1918, link-local, ULA, CGNAT) are refused
//     unless the shipped config says connectorPrivateNetwork: true — an
//     owner/deploy-time decision, never a page setting; cloud metadata
//     addresses are refused always.
// Names are resolved here (every address must pass); the sidecar resolves
// again when it connects, so a name that changes its answer later is still
// caught there.
//
// No 'electron' import: unit-tested in plain Node.

import dns from "node:dns/promises";
import net from "node:net";

export type RemoteRefusal = "invalid-url" | "https-required" | "userinfo-in-url" | "unresolvable" | "internal" | "metadata";

export interface RemoteTargetCheck {
  ok: boolean;
  reason?: RemoteRefusal;
  /** scheme://host[:port] — what the confirmation names; never the query. */
  origin?: string;
  host?: string;
  addresses?: string[];
}

const METADATA_V4 = new Set(["169.254.169.254", "100.100.100.200"]);
const METADATA_V6 = new Set(["fd00:ec2::254"]);

function v4Parts(ip: string): number[] | null {
  if (net.isIPv4(ip)) return ip.split(".").map(Number);
  const m = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(ip);
  return m ? m[1]!.split(".").map(Number) : null;
}

export function isMetadataIp(ip: string): boolean {
  const v4 = v4Parts(ip);
  if (v4) return METADATA_V4.has(v4.join("."));
  return METADATA_V6.has(ip.toLowerCase());
}

/** Loopback, private, link-local, unique-local, CGNAT, unspecified, multicast. */
export function isInternalIp(ip: string): boolean {
  const v4 = v4Parts(ip);
  if (v4) {
    const [a, b] = v4 as [number, number];
    return a === 10 || a === 127 || a === 0 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  if (!net.isIPv6(ip)) return true;
  const l = ip.toLowerCase();
  if (l === "::1" || l === "::") return true;
  if (/^fe[89ab]/.test(l)) return true; // link-local fe80::/10
  if (/^f[cd]/.test(l)) return true; // unique-local fc00::/7
  if (/^ff/.test(l)) return true; // multicast
  return false;
}

/** The scheme/shape rule alone — synchronous, no resolution. */
export function classifyRemoteUrl(raw: string): { ok: true; url: URL } | { ok: false; reason: RemoteRefusal } {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return { ok: false, reason: "invalid-url" };
  }
  if (url.protocol !== "https:") return { ok: false, reason: "https-required" };
  if (url.username || url.password) return { ok: false, reason: "userinfo-in-url" };
  if (!url.hostname) return { ok: false, reason: "invalid-url" };
  return { ok: true, url };
}

export async function checkRemoteTarget(
  raw: string,
  opts: { allowPrivate: boolean; resolve?: (host: string) => Promise<string[]> },
): Promise<RemoteTargetCheck> {
  const shape = classifyRemoteUrl(raw);
  if (!shape.ok) return { ok: false, reason: shape.reason };
  const host = shape.url.hostname.replace(/^\[|\]$/g, "");
  let addresses: string[];
  if (net.isIP(host)) addresses = [host];
  else {
    try {
      const resolve = opts.resolve ?? (async (h) => (await dns.lookup(h, { all: true })).map((r) => r.address));
      addresses = await resolve(host);
    } catch {
      addresses = [];
    }
  }
  if (addresses.length === 0) return { ok: false, reason: "unresolvable", host };
  for (const a of addresses) {
    if (isMetadataIp(a)) return { ok: false, reason: "metadata", host, addresses };
    if (!opts.allowPrivate && isInternalIp(a)) return { ok: false, reason: "internal", host, addresses };
  }
  return { ok: true, origin: shape.url.origin, host, addresses };
}
