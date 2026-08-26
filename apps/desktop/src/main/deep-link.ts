// Custom-scheme URL parsing (E2). Two shapes arrive on the dev scheme:
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
// Ported from SranDesktop.Application/Services/OAuthDeepLink.cs (same ticket
// regex, same "connector" exclusion, same length cap). No 'electron' import.

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
