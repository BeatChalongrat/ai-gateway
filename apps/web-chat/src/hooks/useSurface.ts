// FIX43 Part B — which surface is this page on, once it is known.
//
// useHostedMode() answers false during hydration on BOTH surfaces (the server
// HTML has no window), so a page that branches on it alone shows the web
// version to the desktop for one tick — and fires the web version's effects.
// This hook answers null until the first client effect has run, then the
// runtime truth (window.sranHost from the Electron preload, read once by
// HOSTED_MODE). A page renders nothing for that tick and never the wrong
// half. Same signal useDesktopOnlyRoute relies on; that hook still exists
// for the routes that have no web half at all (connectors, writing style).
import { useEffect, useState } from "react";

import { HOSTED_MODE } from "@/lib/auth";

export type Surface = "desktop" | "web";

export function useSurface(): Surface | null {
  const [surface, setSurface] = useState<Surface | null>(null);
  useEffect(() => {
    setSurface(HOSTED_MODE ? "desktop" : "web");
  }, []);
  return surface;
}
