import { Noto_Sans_Thai } from "next/font/google";

import "./marketing.css";

import { LanguageProvider } from "./_components/LanguageProvider";
import { MarketingFooter } from "./_components/MarketingFooter";
import { MarketingNav } from "./_components/MarketingNav";
import { ScrollReveal } from "./_components/ScrollReveal";

// Thai glyph coverage for the marketing surface. Inter (from the root
// layout) stays first in the stack; Noto Sans Thai fills in Thai
// codepoints, matching the approved mockup's pairing.
const notoThai = Noto_Sans_Thai({
  subsets: ["thai"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-noto-thai",
});

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className={`marketing-root ${notoThai.variable}`}>
      {/* Without JS the reveal observer never runs — show everything. */}
      <noscript>
        <style>{`.reveal{opacity:1;transform:none}.steps::after{width:66.66%}`}</style>
      </noscript>
      <LanguageProvider>
        <MarketingNav />
        <main>{children}</main>
        <MarketingFooter />
      </LanguageProvider>
      {/* Wires IntersectionObserver to ``.reveal`` and ``.steps``.
          Mounted once per layout — children stay server components
          unless they opt into the language context. */}
      <ScrollReveal />
    </div>
  );
}
