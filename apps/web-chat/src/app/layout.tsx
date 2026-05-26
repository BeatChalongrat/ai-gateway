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
    icon: "/icons/icon.svg",
    apple: "/icons/icon.svg",
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
  themeColor: "#0F1835",
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
