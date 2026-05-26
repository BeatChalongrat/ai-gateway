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

const loginSlides: Slide[] = [
  {
    headlineTop: "Your AI infrastructure,",
    headlineBottom: "running smoothly.",
    subtext:
      "Secure, smart, and efficient inference for every AI application.",
    stats: [
      {
        value: (
          <>
            <span className="av-pulse-dot" aria-hidden="true" />847
          </>
        ),
        label: "req/sec",
      },
      { value: "14", label: "Providers" },
      {
        value: (
          <>
            187<span className="av-ms">ms</span>
          </>
        ),
        label: "p50 latency",
      },
    ],
  },
  {
    headlineTop: "Data protection,",
    headlineBottom: "built in.",
    subtext:
      "30 detectors scan every request for credentials, PII, and sensitive data — before it leaves your app.",
    stats: [
      { value: "30", label: "Detectors" },
      { value: "SOC 2", label: "Compliant" },
      { value: "0", label: "Data retained" },
    ],
  },
  {
    headlineTop: "Every request,",
    headlineBottom: "fully tracked.",
    subtext:
      "Real-time logs, cost breakdown by model, latency percentiles, and alerts — all in one dashboard.",
    stats: [
      { value: "Real-time", label: "Alerts" },
      { value: "90-day", label: "Logs" },
      {
        value: (
          <>
            99.98<span className="av-ms">%</span>
          </>
        ),
        label: "Uptime",
      },
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
    </div>
  );
}
