// FIX20 §6.5 — hosted mode for COMPONENTS.
//
// HOSTED_MODE (lib/auth) is decided at runtime from `window.sranHost`, which
// only exists in the browser (the Electron preload injects it). Server-
// rendered HTML therefore always reflects "not hosted"; a component that read
// the constant during render would hydrate against mismatching markup. This
// hook returns the server value (false) for the hydration pass and the real
// value right after, which is the documented useSyncExternalStore behaviour —
// no mismatch warning, and the desktop-only UI appears within the same tick.
//
// Effects that must run only in hosted mode must depend on the hook's value
// (not `[]`): the first commit is the hydration pass.

import { useSyncExternalStore } from "react";

import { HOSTED_MODE } from "@/lib/auth";

const subscribe = () => () => {};
const getSnapshot = () => HOSTED_MODE;
const getServerSnapshot = () => false;

export function useHostedMode(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
