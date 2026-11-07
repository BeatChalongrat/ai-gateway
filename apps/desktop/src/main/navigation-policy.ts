// Navigation allow-list, transposed from the WebView2 shell's NavigationPolicy
// (SranDesktop.Shell/Hosting/NavigationPolicy.cs) — same semantics, same
// refusals, so the two shells cannot drift apart on what "outside" means.
//
// Origins are compared as ORIGINS (scheme://host[:port], via the WHATWG URL
// parser, default ports normalised) — never as string prefixes:
// "https://evil.com/?x=https://good" and "http://good@evil.com/" both pass a
// startsWith check and both fail here.
//
// No 'electron' import: pinned by test/navigation-policy.test.ts in plain Node.

export interface NavigationPolicy {
  readonly allowedOrigins: ReadonlySet<string>;
  /** Not allowed — a navigation towards it is classified as the re-auth
   * signal (E2 answers it host-side). Never makes a navigation allowed. */
  readonly portalOrigin: string | null;
}

export type RefusalReason =
  | "outside-allow-list"
  | "portal-reauth"
  | "unsupported-scheme"
  | "unparseable";

export type NavigationVerdict =
  | { readonly allowed: true }
  | { readonly allowed: false; readonly reason: RefusalReason };

const ALLOWED = Object.freeze({ allowed: true } as const);

/** scheme://host[:port] of an http(s) URL, or null for anything else. */
export function originOf(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  // URL.origin already normalises default ports and lower-cases the host.
  return parsed.origin;
}

/** Validates one configured origin entry, exactly as the C# side does:
 * absolute, http(s), no wildcard, no path/query/fragment/userinfo. */
export function parseOriginEntry(raw: string, settingName: string): string {
  const value = raw.trim();
  if (value.includes("*")) {
    throw new Error(`Wildcards are not allowed in ${settingName} (got '${raw}').`);
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${settingName} entry is not an absolute URL: '${raw}'.`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${settingName} entry must be an http(s) origin: '${raw}'.`);
  }
  if (
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    parsed.username !== "" ||
    parsed.password !== ""
  ) {
    throw new Error(`${settingName} entry must be an origin, not a URL: '${raw}'.`);
  }
  return parsed.origin;
}

export function createNavigationPolicy(
  allowedOrigins: readonly string[],
  portalOrigin?: string | null,
): NavigationPolicy {
  const origins = new Set<string>();
  for (const raw of allowedOrigins) origins.add(parseOriginEntry(raw, "allowedOrigins"));
  if (origins.size === 0) {
    throw new Error("allowedOrigins must contain at least one origin.");
  }
  const portal = portalOrigin ? parseOriginEntry(portalOrigin, "portalOrigin") : null;
  if (portal !== null && origins.has(portal)) {
    throw new Error(
      `portalOrigin '${portal}' must not also be in allowedOrigins — ` +
        "the portal is the re-auth signal, not a destination.",
    );
  }
  return Object.freeze({ allowedOrigins: origins, portalOrigin: portal });
}

function classify(policy: NavigationPolicy, url: string): NavigationVerdict {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { allowed: false, reason: "unparseable" };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { allowed: false, reason: "unsupported-scheme" };
  }
  if (policy.allowedOrigins.has(parsed.origin)) return ALLOWED;
  if (policy.portalOrigin !== null && parsed.origin === policy.portalOrigin) {
    return { allowed: false, reason: "portal-reauth" };
  }
  return { allowed: false, reason: "outside-allow-list" };
}

/** Top-level navigation (will-navigate / will-redirect): the allow-list plus
 * inert about:blank. */
export function classifyTopLevel(policy: NavigationPolicy, url: string): NavigationVerdict {
  if (url === "about:blank") return ALLOWED;
  return classify(policy, url);
}

/** Sub-frame navigation (will-frame-navigate): the allow-list plus the two
 * inert about: forms. Sandboxed srcdoc artifact frames are about:srcdoc. */
export function classifyFrame(policy: NavigationPolicy, url: string): NavigationVerdict {
  if (url === "about:blank" || url === "about:srcdoc") return ALLOWED;
  return classify(policy, url);
}

/** window.open / target=_blank: only http(s) may go to the system browser;
 * every other scheme (file:, javascript:, custom) is dropped. */
export function isExternalHttpUrl(url: string): boolean {
  return originOf(url) !== null;
}
