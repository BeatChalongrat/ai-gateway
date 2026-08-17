import type { Metadata } from "next";
import { Suspense } from "react";

import "./login.css";
import AuthSplitLayout from "./_components/AuthSplitLayout";
import LoginForm from "./_components/LoginForm";
import VisualPanel, { type Slide } from "./_components/VisualPanel";

export const metadata: Metadata = {
  title: "Sign in — SRAN",
  description: "Sign in to your SRAN AI Gateway dashboard.",
};

// Every figure here must be verifiable against the running system —
// no throughput, latency, uptime, or certification claims.
const loginSlides: Slide[] = [
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

export default function LoginPage() {
  // ``useSearchParams`` inside LoginForm requires a Suspense
  // boundary so Next.js can prerender the static shell without
  // bailing on the whole route. The fallback re-uses the form
  // markup with the action disabled — visually indistinguishable
  // from the live form for the brief paint before search params
  // resolve.
  return (
    <AuthSplitLayout
      visual={<VisualPanel slides={loginSlides} />}
      form={
        <Suspense fallback={<LoginFormSkeleton />}>
          <LoginForm />
        </Suspense>
      }
    />
  );
}

function LoginFormSkeleton() {
  return (
    <div className="auth-form-card" aria-hidden="true">
      <div className="af-eyebrow">Sign in</div>
      <h1 className="af-title">Welcome back</h1>
      <p className="af-desc">
        Enter your credentials to access your SRAN dashboard.
      </p>
      <div className="af-form">
        <div className="af-field">
          <div className="af-skeleton af-skeleton-label skeleton" />
          <div className="af-skeleton af-skeleton-input skeleton" />
        </div>
        <div className="af-field">
          <div className="af-skeleton af-skeleton-label skeleton" />
          <div className="af-skeleton af-skeleton-input skeleton" />
        </div>
        <div className="af-skeleton af-skeleton-button skeleton" />
      </div>
    </div>
  );
}
