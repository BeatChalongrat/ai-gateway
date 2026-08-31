// FIX34.1 Part F — desktop-only ROUTES on the web surface.
//
// The sidebar already hides the desktop-only entries when the page is not
// hosted (useHostedMode), so the only way a browser user reaches
// /chat/connectors, /chat/settings, /chat/writing-style or /quick is a typed
// URL, an old bookmark or a stale link. Those pages used to answer with a
// wall ("This screen is available in the SRAN Desktop app.") — a dead end
// that points at a Windows-only, unsigned pre-release. ChatGPT / Claude never
// do that on the web: a feature that isn't there has no page. So on the web
// the route silently lands on a real page instead; on the desktop nothing
// changes.
//
// Hydration: useHostedMode() is false for the hydration pass on BOTH
// surfaces (server HTML has no window), so an effect keyed on that value
// would bounce the desktop too. The decision therefore reads the module
// constant HOSTED_MODE — evaluated once in the browser, before any page
// script, from the Electron preload's window.sranHost — which is exactly the
// runtime truth; effects never run on the server, so reading it there is
// safe. The hook returns the hydration-safe `hosted` so the page renders
// nothing until it knows (desktop: one tick; web: until the redirect lands).
//
// The redirect is a plain location.replace, not next/navigation: the entry
// was a full navigation anyway, the dead route must not stay in history, and
// it keeps the hook free of the app-router context (the /quick page is also
// rendered stand-alone in tests).

import { useEffect } from "react";

import { HOSTED_MODE } from "@/lib/auth";

import { useHostedMode } from "./useHostedMode";

/** Indirection so tests can observe the redirect (jsdom cannot navigate). */
export const desktopOnlyRedirect = {
  to(url: string): void {
    window.location.replace(url);
  },
};

export function useDesktopOnlyRoute(fallback: string = "/chat"): boolean {
  const hosted = useHostedMode();
  useEffect(() => {
    // Both signals must say "not hosted": HOSTED_MODE is the runtime truth,
    // `hosted` is the same value seen through the hydration-safe hook.
    if (!HOSTED_MODE && !hosted) desktopOnlyRedirect.to(fallback);
  }, [fallback, hosted]);
  return hosted;
}
