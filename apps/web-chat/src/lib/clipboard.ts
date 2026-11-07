// FIX47.6 Part D — one clipboard write path, with an honest verdict.
//
// The old copy handler tried navigator.clipboard.writeText and ended in
// `catch { /* clipboard blocked */ }` — in the desktop shell, where the
// hardened session denies Chromium permission requests, writeText is
// DEFINED but rejects (NotAllowedError), so every copy failed with no
// icon change, no message, nothing (measured live in FIX47.6). The
// execCommand fallback also only ran when writeText was undefined, and
// its boolean return was ignored.
//
// This helper tries the async API first and falls back to the
// hidden-textarea execCommand path on ANY failure (not just absence),
// honouring execCommand's boolean. Callers get true/false and must
// surface the failure — silent no-ops are the FIX44 defect class.

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through — permission denied is a state, not a dead end */
    }
  }
  return execCommandCopy(text);
}

function execCommandCopy(text: string): boolean {
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  // Keep focus restorable: remember what the user had focused.
  const prev = document.activeElement;
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  if (prev instanceof HTMLElement) prev.focus?.();
  return ok;
}
