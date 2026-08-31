// FIX34 Part B/C — deep-link parameters the desktop host puts on a page URL.
//
//   /chat?q=…              composer PREFILL from sran://…/new?q=… — the text is
//                          placed in the box and never sent (Anthropic's rule
//                          for claude://…/new?q=: "Prompts are prefilled but
//                          not automatically sent");
//   /chat/{id}?attach=…    a chat-edge file id the host already uploaded to
//                          that conversation (Explorer "Ask SRAN about this
//                          file") — staged as a ready chip;
//   /chat/projects?project=…  the project to expand.
//
// Each helper reads window.location ONCE and removes the parameter from the
// address bar (history.replaceState) so a reload does not replay it. Read
// from window rather than useSearchParams so the client pages need no
// Suspense boundary. Pure enough to unit-test with jsdom.

/** Same cap the host applies (deep-link.ts MAX_PREFILL_CHARS); belt and braces. */
export const MAX_PREFILL_CHARS = 14_000;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

function takeParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    return null;
  }
  if (!params.has(name)) return null;
  const value = params.get(name);
  params.delete(name);
  try {
    const rest = params.toString();
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${rest ? `?${rest}` : ""}${window.location.hash}`);
  } catch {
    /* a locked-down history is not worth failing the page */
  }
  return value;
}

/** The composer prefill, or null. Never sends anything. */
export function consumeQueryPrefill(): string | null {
  const raw = takeParam("q");
  if (raw === null) return null;
  const cleaned = raw.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
  if (cleaned.trim() === "") return null;
  const points = Array.from(cleaned);
  return points.length > MAX_PREFILL_CHARS ? points.slice(0, MAX_PREFILL_CHARS).join("") : cleaned;
}

/** A chat-edge file id to stage, or null when absent / not id-shaped. */
export function consumeQueryAttach(): string | null {
  const raw = takeParam("attach");
  return raw !== null && ID_PATTERN.test(raw) ? raw : null;
}

/** A project id to expand, or null. */
export function consumeQueryProject(): string | null {
  const raw = takeParam("project");
  return raw !== null && ID_PATTERN.test(raw) ? raw : null;
}
