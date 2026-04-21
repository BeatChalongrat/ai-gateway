import type { Metadata } from "next";

import { CTASection } from "./_components/CTASection";
import { Features } from "./_components/Features";
import HashScrollHandler from "./_components/HashScrollHandler";
import { Hero } from "./_components/Hero";
import { HowItWorks } from "./_components/HowItWorks";
import { SocialProof } from "./_components/SocialProof";

export const metadata: Metadata = {
  title: "SRAN AI Gateway — AI infrastructure, simplified",
  description:
    "One endpoint for every AI model. Automatic cost savings, built-in data protection, and analytics that make sense.",
};

export default function LandingPage() {
  return (
    <>
      <HashScrollHandler />
      <Hero />
      <SocialProof />
      <Features />
      <HowItWorks />
      <CTASection />
    </>
  );
}
