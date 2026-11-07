// Invite landing page (Pattern-2 shareable-link flow, 2026-04-24).
//
// A visitor reaching /invite/{code} hits this page server-side:
//   1. We resolve the code against the public preview endpoint.
//   2. Valid code → render the Signup panel pre-filled with the code
//      and a "Joining {tenant} as {role}" banner.
//   3. Invalid code → render the same visual shell with an error card
//      ("This invite has expired or been revoked") and a link back to
//      a plain /signup so the user can still create their own team.
//
// We deliberately share the existing auth-split layout so the look
// matches /signup — the only difference is the header copy and the
// invite-code field being non-editable. Keeping the visual continuity
// means a teammate following a Slack link doesn't land on a page that
// feels unrelated to "SRAN".

import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight } from "lucide-react";

import "../../login/login.css";
import "../../signup/signup.css";

import VisualPanel, { type Slide } from "../../login/_components/VisualPanel";
import SignupForm from "../../signup/_components/SignupForm";

export const metadata: Metadata = {
  title: "Join your team — SRAN",
  description: "Accept a team invite on SRAN AI Gateway.",
};

interface InvitePreview {
  valid: boolean;
  tenant_name?: string;
  tenant_id?: string;
  role?: string;
  inviter_name?: string | null;
  expires_at?: string | null;
}

async function fetchPreview(code: string): Promise<InvitePreview> {
  // Server-side fetch hits the same backend the /api proxy points at.
  // In Docker-compose the backend host is literally `backend:8080`;
  // the NEXT_PUBLIC_BACKEND_INTERNAL_URL override lets a different
  // deployment name it something else without editing source.
  const base =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_BACKEND_INTERNAL_URL ||
    "http://backend:8080";
  try {
    const res = await fetch(
      `${base.replace(/\/$/, "")}/portal/auth/invite/${encodeURIComponent(code)}`,
      { cache: "no-store" }
    );
    if (!res.ok) return { valid: false };
    return (await res.json()) as InvitePreview;
  } catch {
    return { valid: false };
  }
}

// Same verified-facts slides as /login and /signup — no made-up metrics.
const inviteSlides: Slide[] = [
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

export default async function InviteLandingPage({
  params,
}: {
  params: { code: string };
}) {
  const preview = await fetchPreview(params.code);

  if (!preview.valid) {
    return (
      <div className="auth-root">
        <div className="auth-split">
          <VisualPanel slides={inviteSlides} />
          <section className="sv-form-side">
            <div className="sv-form-wrap">
              <Link
                href="/"
                className="auth-back-home"
                aria-label="Back to home"
              >
                <span aria-hidden="true">←</span> Back to home
              </Link>
              <div
                className="sv-form-header anim d1"
                style={{ marginBottom: 24 }}
              >
                {/* Error eyebrow: red-tint background instead of the navy
                    pill, so the red text reads cleanly. */}
                <div
                  className="sv-form-eyebrow"
                  style={{ background: "rgba(248,113,113,0.15)", color: "#F87171" }}
                >
                  Invite unavailable
                </div>
                <h1>This invite link isn&apos;t active.</h1>
                <p>
                  The link may have expired or been revoked by your
                  team owner. Ask them for a fresh link, or create
                  your own team below.
                </p>
              </div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                <Link
                  href="/signup"
                  className="sv-submit"
                  style={{ display: "inline-flex", textDecoration: "none" }}
                >
                  Create your own team
                  <span className="sv-arrow" aria-hidden="true"><ArrowRight size={16} /></span>
                </Link>
                <Link
                  href="/login"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    padding: "12px 20px",
                    borderRadius: 10,
                    // Was white-on-white (invisible) on the light .sv-form-side.
                    // Use theme tokens so it reads as a legible secondary button.
                    border: "1px solid var(--line-2)",
                    background: "var(--bg-2)",
                    color: "var(--text)",
                    textDecoration: "none",
                    fontSize: 14,
                    fontWeight: 500,
                  }}
                >
                  Sign in instead
                </Link>
              </div>
            </div>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-root">
      <div className="auth-split">
        <VisualPanel slides={inviteSlides} />
        <SignupForm
          presetInviteCode={params.code}
          inviteContext={{
            tenantName: preview.tenant_name || "your team",
            role: preview.role || "member",
            inviterName: preview.inviter_name ?? null,
          }}
        />
      </div>
    </div>
  );
}
