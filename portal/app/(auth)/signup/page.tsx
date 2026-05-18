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
    "Create your free SRAN account — 200,000 tokens, no card required.",
};

const signupSlides: Slide[] = [
  {
    headlineTop: "Build with AI,",
    headlineBottom: "without the overhead.",
    subtext:
      "Drop in one endpoint and get security, routing, and observability out of the box.",
    stats: [
      { value: "200K", label: "Signup bonus" },
      { value: "14", label: "Providers" },
      { value: "5 min", label: "Setup" },
    ],
  },
  {
    headlineTop: "One endpoint,",
    headlineBottom: "every AI model.",
    subtext:
      "Route automatically to the best model for each request — based on cost, latency, or quality.",
    stats: [
      { value: "14", label: "Providers" },
      { value: "30", label: "Detectors" },
      { value: "No expiry", label: "Tokens" },
    ],
  },
  {
    headlineTop: "Pay as you go,",
    headlineBottom: "never subscribe.",
    subtext:
      "Buy tokens once, use them forever. No monthly fees. No surprise bills.",
    stats: [
      { value: "$10", label: "Starter pack" },
      { value: "1M–20M", label: "Per pack" },
      { value: "0", label: "Subscriptions" },
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
