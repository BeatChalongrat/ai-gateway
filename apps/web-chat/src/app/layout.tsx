import type { Metadata, Viewport } from "next";

import "./globals.css";
import localFont from "next/font/local";
import { AppProviders } from "./AppProviders";

// Fonts are VENDORED, not fetched from Google at build time.
//
// next/font/google downloads the woff2 during `next build`, which made the
// build require outbound access to fonts.googleapis.com — it failed in the
// container and would fail in any air-gapped or egress-restricted network,
// which is exactly where a DLP gateway gets deployed. Runtime was never
// affected (Next self-hosts what it fetched); this removes the BUILD-time
// coupling.
//
// The bytes are the same ones Next was downloading: Google serves each of
// these families as a single variable woff2 covering every weight we ask
// for (all four Outfit weights resolved to one URL, both JetBrains weights
// to another), so the vendored file IS the file, and rendering is
// unchanged. Weight RANGES below describe that variable axis; the weights
// actually used by the app (Outfit 400/500/600/700, JetBrains 400/500) sit
// inside them.
//
// Latin subset only, matching the previous `subsets: ["latin"]`. Thai still
// falls back to the OS face — bundling a Thai webfont is a separate
// decision, now possible because this directory exists.
//
// Licences: both families are SIL OFL 1.1 (see OFL-Outfit.txt and
// OFL-JetBrainsMono.txt next to the files), which permits redistribution
// and bundling; the OFL requires the notices to travel with the fonts, so
// they are committed here rather than referenced.
const outfit = localFont({
  src: [{ path: "./fonts/Outfit-latin-variable.woff2", weight: "100 900", style: "normal" }],
  variable: "--font-outfit",
  display: "swap",
});
const jetbrains = localFont({
  src: [
    { path: "./fonts/JetBrainsMono-latin-variable.woff2", weight: "100 800", style: "normal" },
  ],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "SRAN Chat",
  description: "Secure AI Chat powered by SRAN AI Gateway",
  manifest: "/manifest.json",
  applicationName: "SRAN Chat",
  appleWebApp: {
    capable: true,
    title: "SRAN Chat",
    statusBarStyle: "black-translucent",
  },
  icons: {
    // SRAN cat logo. The PNG is a white cat on its own dark-navy tile,
    // so it stays visible on both light and dark browser tabs (no
    // transparent background to disappear against Chrome's dark tab
    // strip). Pre-sized favicons keep the tab icon crisp + light;
    // full-res sran-logo.png backs the Apple touch icon.
    icon: [
      { url: "/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: "/sran-logo.png",
  },
};

// ``viewport-fit=cover`` is what lets env(safe-area-inset-*) return
// non-zero values on iPhones with a notch — without it the safe-area
// padding rules in globals.css silently no-op.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: "#171717",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${outfit.variable} ${jetbrains.variable}`}
      suppressHydrationWarning
    >
      <body className="min-h-screen bg-bg-base text-text-primary antialiased">
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
