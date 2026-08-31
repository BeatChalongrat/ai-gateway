// Custom-scheme URL parsing (E2 + FIX34 Part B). Shapes that arrive on the
// app's scheme (sran:// from FIX34; the dev builds used sran-dev://):
//
//   <scheme>://oauth/callback?ticket=<hex>   the system-browser login result
//                                            (RFC 8252 loopback-less variant —
//                                            the ticket is opaque; the nonce
//                                            that redeems it never leaves the
//                                            host process)
//   <scheme>://signin | <scheme>://cancel     INTENTS from the shell-owned
//                                            signed-out view, delivered as a
//                                            refused navigation — the page
//                                            has no bridge, and E2 adds none.
//
//   FIX34 Part B — app links, Claude's shape (claude://claude.ai/new?q=…,
//   claude://claude.ai/chat/{id}, claude://claude.ai/project/{id}: one scheme,
//   a host that mirrors the web domain, paths that match the web routes):
//   <scheme>://<web host>/new                 a new chat
//   <scheme>://<web host>/new?q=<encoded>     … with the composer PREFILLED —
//                                            never sent (Anthropic: "Prompts
//                                            are prefilled but not
//                                            automatically sent"; a web page
//                                            must not be able to make the app
//                                            ask anything with one click)
//   <scheme>://<web host>/new?file=<path>     … with a local file to attach
//                                            (the Explorer menu, Part C; the
//                                            host confirms EVERY time)
//   <scheme>://<web host>/chat/{id}           open a conversation
//   <scheme>://<web host>/project/{id}        open a project
//
// Ported from SranDesktop.Application/Services/OAuthDeepLink.cs (same ticket
// regex, same "connector" exclusion, same length cap). No 'electron' import.
// Malformed input → null, silently (the caller logs); nothing here throws.

const MAX_URL_LENGTH = 2048;
const TICKET_PATTERN = /^[a-f0-9]{32,128}$/;

export type Intent = "signin" | "cancel";

export function parseLoginTicket(url: string | null | undefined, scheme: string): string | null {
  if (!url || url.length > MAX_URL_LENGTH) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== `${scheme}:`) return null;
  if (parsed.host !== "oauth" || parsed.pathname !== "/callback") return null;
  if (parsed.searchParams.has("connector")) return null; // connector variant — not ours
  const ticket = parsed.searchParams.get("ticket");
  return ticket !== null && TICKET_PATTERN.test(ticket) ? ticket : null;
}

export function parseIntent(url: string | null | undefined, scheme: string): Intent | null {
  if (!url || url.length > MAX_URL_LENGTH) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== `${scheme}:`) return null;
  if (parsed.search !== "" || parsed.hash !== "") return null;
  const path = parsed.pathname === "/" ? "" : parsed.pathname;
  if (path !== "") return null;
  return parsed.host === "signin" ? "signin" : parsed.host === "cancel" ? "cancel" : null;
}

// ── FIX34 Part B: app links ─────────────────────────────────────────────────

/** An app link arrives as ONE argv entry; Windows caps a command line at
 * 32,767 characters, so nothing longer can ever reach us. The OAuth/intent
 * parsers keep their own 2048 cap above (unchanged). */
export const MAX_APP_LINK_LENGTH = 32_767;
/** Claude truncates a `q` prefill at ~14,000 characters; same here (after decoding). */
export const MAX_PREFILL_CHARS = 14_000;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const MAX_FILE_PATH_LENGTH = 1024;

export type AppLink =
  | { readonly kind: "new"; readonly q: string | null; readonly file: string | null }
  | { readonly kind: "chat"; readonly id: string }
  | { readonly kind: "project"; readonly id: string };

/**
 * Parses an app link. `expectedHost` is the web app's host (from pageUrl,
 * e.g. "localhost:3002" or "chat.example.com") — the deep-link host mirrors
 * it the way claude:// mirrors claude.ai. Anything else → null.
 */
export function parseAppLink(url: string | null | undefined, scheme: string, expectedHost: string): AppLink | null {
  if (!url || url.length > MAX_APP_LINK_LENGTH) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== `${scheme}:`) return null;
  if (parsed.host.toLowerCase() !== expectedHost.toLowerCase() || parsed.host === "") return null;
  if (parsed.hash !== "") return null;
  const segments = parsed.pathname.split("/").filter((s) => s !== "");
  if (segments.length === 1 && segments[0] === "new") {
    return { kind: "new", q: prefillOf(parsed.searchParams.get("q")), file: filePathOf(parsed.searchParams.get("file")) };
  }
  if (segments.length === 2 && (segments[0] === "chat" || segments[0] === "project")) {
    const id = segments[1]!;
    if (!ID_PATTERN.test(id)) return null;
    return { kind: segments[0], id };
  }
  return null;
}

/** The composer prefill: decoded by the URL parser, control characters other
 * than newline/tab removed, cut at MAX_PREFILL_CHARS (code points, so a
 * Thai or emoji prompt is not cut mid-character). Empty → null. */
export function prefillOf(raw: string | null): string | null {
  if (raw === null) return null;
  // eslint-disable-next-line no-control-regex
  const cleaned = raw.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
  if (cleaned.trim() === "") return null;
  const points = Array.from(cleaned);
  return points.length > MAX_PREFILL_CHARS ? points.slice(0, MAX_PREFILL_CHARS).join("") : cleaned;
}

/** A plausible absolute Windows file path, or null. Deny-by-shape: drive
 * letter + backslash, no `..` segment, no wildcard / redirection / control
 * characters, bounded length. Whether it EXISTS and is allowed is the
 * host's decision, after the user confirms. */
export function filePathOf(raw: string | null): string | null {
  if (raw === null) return null;
  const value = raw.trim();
  if (value.length === 0 || value.length > MAX_FILE_PATH_LENGTH) return null;
  if (!/^[A-Za-z]:\\/.test(value)) return null;
  // eslint-disable-next-line no-control-regex
  if (/[<>"|?*\x00-\x1f\x7f]/.test(value)) return null;
  if (value.split(/[\\/]/).some((seg) => seg === "..")) return null;
  if (/[\\/]$/.test(value)) return null; // a folder, not a file
  return value;
}
