import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "./globals.css";

// Self-hosted via next/font so the marketing page can use Inter for
// body and a tabular monospace for .flow-stat-val without adding
// render-blocking <link rel="stylesheet"> requests. The spec asks for
// Geist Mono, but Next 14.2.15 doesn't ship it from
// ``next/font/google`` (it landed in 14.3+). JetBrains Mono is the
// closest-matching tabular grotesque already used elsewhere in this
// codebase. We expose it under the same ``--font-geist-mono`` CSS
// variable so the marketing CSS doesn't have to change when we
// eventually upgrade Next.
const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  variable: "--font-inter",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-geist-mono",
});

export const metadata: Metadata = {
  title: "SRAN Portal",
  description: "SRAN AI Gateway — user portal",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
