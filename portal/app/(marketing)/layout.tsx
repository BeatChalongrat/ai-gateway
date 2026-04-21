import "./marketing.css";

import { MarketingFooter } from "./_components/MarketingFooter";
import { MarketingNav } from "./_components/MarketingNav";
import { ScrollReveal } from "./_components/ScrollReveal";

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="marketing-root">
      <MarketingNav />
      <main>{children}</main>
      <MarketingFooter />
      {/* Wires IntersectionObserver to ``.reveal`` and ``.steps``.
          Mounted once per layout — children stay server components. */}
      <ScrollReveal />
    </div>
  );
}
