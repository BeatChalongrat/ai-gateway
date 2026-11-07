"use client";

// /chat/settings — one route, two halves, decided at runtime (FIX43 Part B).
//
// The desktop half (DeviceSettings) is the security-critical device page:
// tray, start with Windows, folder grants, approved sites, the browser bridge.
// The web half (WebSettings) is what a browser user gets: appearance, account,
// and the doors to the account-wide pages. The split is by SURFACE, not by
// disabling rows — a device row never renders in a browser, greyed or not.
// Until the surface is known (one client tick) the page renders nothing, so
// neither half's effects run on the other side.

import { useSurface } from "@/hooks/useSurface";

import { DeviceSettings } from "./DeviceSettings";
import { WebSettings } from "./WebSettings";

export default function SettingsPage() {
  const surface = useSurface();
  if (surface === null) return null;
  return surface === "desktop" ? <DeviceSettings /> : <WebSettings />;
}
