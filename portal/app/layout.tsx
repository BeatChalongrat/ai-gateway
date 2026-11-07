import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { ThemeProvider } from "@/components/ThemeProvider";
import { ToastProvider } from "@/components/Toast";
import "./globals.css";

// Inline before-hydration script: read sran-portal-theme from
// localStorage and stamp data-theme on <html> *before* React renders,
// so the first paint already matches the user's saved theme. Without
// this the page flashes light → dark on every refresh in dark mode.
const themeBootScript = `
  (function () {
    try {
      var t = localStorage.getItem("sran-portal-theme") || "light";
      document.documentElement.setAttribute("data-theme", t);
    } catch (e) {}
  })();
`;

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
    <html
      lang="en"
      data-theme="light"
      suppressHydrationWarning
      className={`${inter.variable} ${mono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <ThemeProvider>
          <ToastProvider>{children}</ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
