import type { Metadata } from "next";

// Import login.css FIRST — it provides .auth-root, .auth-split, all
// .av-* visual-panel styles, and the .anim / .d1-.d5 animation classes
// that SignupForm relies on. signup.css then layers form-side (.sv-*)
// overrides on top.
import "../login/login.css";
import "./signup.css";

import VisualPanel, { type Slide } from "../login/_components/VisualPanel";
import SignupForm from "./_components/SignupForm";

export const metadata: Metadata = {
  title: "Create account — SRAN",
  description:
    "Create your SRAN account with your company Google account.",
};

// Same verified-facts slides as /login — every figure must be checkable
// against the running system (no bonuses, prices, or made-up metrics).
const signupSlides: Slide[] = [
  {
    headlineTop: "Data protection,",
    headlineBottom: "built in.",
    subtext:
      "30+ detectors with Thai named-entity recognition mask sensitive values before a request leaves, and restore them in the reply.",
    stats: [
      { value: "30+", label: "Detectors" },
      { value: "PDPA", label: "Aware detection" },
      { value: "AES-256", label: "GCM at rest" },
    ],
  },
  {
    headlineTop: "Your AI infrastructure,",
    headlineBottom: "running smoothly.",
    subtext:
      "Fifteen model providers behind one OpenAI-compatible endpoint — self-hosted on infrastructure your team runs.",
    stats: [
      { value: "15", label: "Providers" },
      { value: "OpenAI", label: "Compatible API" },
      { value: "On-prem", label: "Self-hosted" },
    ],
  },
];

export default function SignupPage() {
  return (
    <div className="auth-root">
      <div className="auth-split">
        <VisualPanel slides={signupSlides} />
        <SignupForm />
      </div>
    </div>
  );
}
