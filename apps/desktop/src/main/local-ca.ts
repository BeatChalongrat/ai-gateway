// FIX133 B — trust for the deployment's own certificate authority.
//
// The stack is served over https by the Caddy front door with a certificate
// issued by the machine's local CA (mkcert). Browsers trust that CA once it
// is in the OS store; the desktop app must not depend on that step — a
// colleague's PC has the app installed but not the CA — so it PINS the CA it
// ships with (`trustedCaFile` in desktop.config.json, staged by
// scripts/dist.js beside the exe) and accepts a server certificate exactly
// when
//
//   * Chromium's own verification failed only because the issuer is unknown
//     (any other failure — expired, wrong host, revoked — stays a failure),
//   * the host is one of the app's own (pageUrl / allowedOrigins, the
//     gateway, chat-edge, the portal), and
//   * the leaf certificate is signed by the pinned CA, is valid now, and
//     names that host.
//
// Everything else keeps Chromium's verdict (`-3`): a public site with a
// valid public certificate is still fine, a forged one is still refused.
// No `ignore-certificate-errors`, no blanket exception, ever.

import fs from "node:fs";
import { X509Certificate } from "node:crypto";
import type { Session } from "electron";

/** Chromium's verification result for "the issuer is not trusted". */
const ERR_CERT_AUTHORITY_INVALID = -202;
/** setCertificateVerifyProc verdicts. */
const VERDICT_OK = 0;
const VERDICT_USE_CHROMIUM = -3;

export interface LocalCaTrust {
  readonly caPem: string;
  readonly hosts: ReadonlySet<string>;
}

export function loadLocalCa(pemPath: string, hosts: Iterable<string>): LocalCaTrust {
  const caPem = fs.readFileSync(pemPath, "utf8");
  // Fail at load, not at the first https request: a wrong file must stop the launch.
  const ca = new X509Certificate(caPem);
  if (!ca.ca) throw new Error(`'trustedCaFile' ${pemPath} is not a CA certificate.`);
  const now = Date.now();
  if (new Date(ca.validTo).getTime() < now) throw new Error(`'trustedCaFile' ${pemPath} has expired (${ca.validTo}).`);
  return { caPem, hosts: new Set([...hosts].map((h) => h.toLowerCase())) };
}

/** The hostnames the app talks to: pageUrl's origin, the allowed origins, the gateway, chat-edge, the portal. */
export function trustedHostsOf(urls: Iterable<string | null | undefined>): Set<string> {
  const out = new Set<string>();
  for (const u of urls) {
    if (!u) continue;
    try {
      out.add(new URL(u).hostname.toLowerCase());
    } catch {
      /* not a URL — ignored */
    }
  }
  return out;
}

/**
 * True when `leafPem` was issued by the pinned CA, is valid now and names
 * `hostname` (DNS or IP SAN). Pure — the unit tests drive it with fixtures.
 */
export function certificateTrustedByLocalCa(leafPem: string, hostname: string, trust: LocalCaTrust, now: number = Date.now()): boolean {
  let leaf: X509Certificate;
  let ca: X509Certificate;
  try {
    leaf = new X509Certificate(leafPem);
    ca = new X509Certificate(trust.caPem);
  } catch {
    return false;
  }
  const host = hostname.toLowerCase();
  if (!trust.hosts.has(host)) return false;
  if (new Date(leaf.validFrom).getTime() > now || new Date(leaf.validTo).getTime() < now) return false;
  if (!leaf.checkIssued(ca)) return false;
  if (!leaf.verify(ca.publicKey)) return false;
  const isIp = /^\d+\.\d+\.\d+\.\d+$/.test(host) || host.includes(":");
  const matched = isIp ? leaf.checkIP(host) : leaf.checkHost(host);
  return matched !== undefined;
}

// FIX134 A3 — the main process's own https calls (sign-in ticket exchange,
// refresh, models, turns: src/main/gateway-client.ts and portal-auth.ts use
// the global `fetch`) run on Node's networking, not Chromium's. Node's fetch
// only knows Node's bundled roots (plus NODE_EXTRA_CA_CERTS, read once at
// process start), so with e57 every call to https://localhost:8080 failed
// with UNABLE_TO_VERIFY_LEAF_SIGNATURE although the page in the window was
// fine. The fix routes the app's OWN https hosts through Electron's
// `net.fetch` — Chromium's stack, where the verifier above (and the OS store)
// apply — and leaves every other URL on Node's fetch, so an external OAuth
// provider or a public MCP server behaves exactly as before.

type FetchLike = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

/** Which fetch serves `input`: Chromium's for the app's own https hosts, Node's for the rest. */
export function fetchFor(input: string | URL | Request, trust: LocalCaTrust, nodeFetch: FetchLike, netFetch: FetchLike): FetchLike {
  let url: URL;
  try {
    url = input instanceof Request ? new URL(input.url) : input instanceof URL ? input : new URL(String(input));
  } catch {
    return nodeFetch;
  }
  return url.protocol === "https:" && trust.hosts.has(url.hostname.toLowerCase()) ? netFetch : nodeFetch;
}

/**
 * Replaces the main process's global `fetch` with the router above. Cookies
 * are never attached by default (`credentials: "omit"`): these calls carry
 * Bearer tokens, and the session cookies injected for the page must not
 * leak into gateway calls on the same host.
 */
export function installNodeFetchTrust(trust: LocalCaTrust, netFetch: FetchLike, log: (line: string) => void, g: { fetch: FetchLike } = globalThis as unknown as { fetch: FetchLike }): void {
  const nodeFetch = g.fetch;
  const viaChromium: FetchLike = (input, init) => netFetch(input, { credentials: "omit", ...(init ?? {}) });
  g.fetch = (input, init) => fetchFor(input, trust, nodeFetch, viaChromium)(input, init);
  log(`main-process https to ${[...trust.hosts].sort().join(", ")} goes through Chromium's network stack (pinned CA); everything else stays on Node's fetch`);
}

/**
 * Installs the certificate verifier on the session. Only the
 * "issuer unknown" failure for one of the app's own hosts is re-examined
 * against the pinned CA; every other case keeps Chromium's verdict.
 */
export function installLocalCaTrust(ses: Session, trust: LocalCaTrust, log: (line: string) => void): void {
  ses.setCertificateVerifyProc((request, callback) => {
    if (request.errorCode === 0 || request.errorCode !== ERR_CERT_AUTHORITY_INVALID) {
      callback(VERDICT_USE_CHROMIUM);
      return;
    }
    const leafPem = request.certificate?.data ?? "";
    if (certificateTrustedByLocalCa(leafPem, request.hostname, trust)) {
      callback(VERDICT_OK);
      return;
    }
    log(`CERTIFICATE REFUSED for ${request.hostname}: not issued by the pinned local CA (${request.verificationResult})`);
    callback(VERDICT_USE_CHROMIUM);
  });
}
