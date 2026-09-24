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
