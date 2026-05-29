import type { Metadata, Viewport } from "next";

import "./globals.css";
import { Outfit, JetBrains_Mono } from "next/font/google";
import { AppProviders } from "./AppProviders";

const outfit = Outfit({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-outfit",
  display: "swap",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
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
