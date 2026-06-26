"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { getSession } from "next-auth/react";
import { Suspense, useEffect, useState } from "react";

import { AlertCircle } from "lucide-react";

import { Logo } from "@/components/Logo";
import { Spinner } from "@/components/Spinner";

// Landing page for a successful NextAuth OAuth sign-in. Reads the
// portal tokens that the signIn/jwt/session callbacks stashed on the
// NextAuth session and POSTs them once to the backend cookie-exchange
// endpoint, which re-issues them as HttpOnly cookies (the same place
// password login lands). After this the session is cookie-based and JS
// never touches the tokens again. Then forwards to ``?redirect=`` (if
// safe) or /dashboard.

interface PortalSession {
  portal_access_token?: string;
  portal_refresh_token?: string;
}

// Same allowlist semantics as LoginForm's safeRedirectTarget — kept
// in lock-step so social login and password login behave identically
// for cross-origin handoff (notably Web Chat on :3002).
function safeRedirectTarget(raw: string | null): string | null {
  if (!raw) return null;
  if (raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\")) {
    return raw;
  }
  try {
    const u = new URL(raw, window.location.origin);
    const allowed = [
      window.location.origin,
      process.env.NEXT_PUBLIC_WEB_CHAT_URL || "http://localhost:3002",
    ].map((o) => new URL(o).origin);
    if (allowed.includes(u.origin)) return u.toString();
  } catch {
    /* fall through */
  }
  return null;
}

// useSearchParams() requires a Suspense boundary so Next can prerender
// the static shell without bailing the whole route during build.
export default function OAuthCompletePage() {
  return (
    <Suspense fallback={<OAuthCompleteFallback />}>
      <OAuthCompleteInner />
    </Suspense>
  );
}

function OAuthCompleteFallback() {
  return (
    <main
      className="min-h-screen flex flex-col items-center justify-center px-4 py-10"
      style={{ background: "var(--bg-base)" }}
    >
      <Logo variant="dark" size="md" />
      <Spinner size={28} className="mt-6" />
      <p className="mt-3 text-sm" style={{ color: "var(--text-secondary)" }}>
        Signing you in…
      </p>
    </main>
  );
}

function OAuthCompleteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const session = (await getSession()) as PortalSession | null;
      if (!session?.portal_access_token || !session?.portal_refresh_token) {
        setError(
          "We couldn't complete your sign-in. Please try again, or use email / password."
        );
        return;
      }

      // Exchange the NextAuth-held tokens for HttpOnly cookies. After
      // this the session is cookie-based — same as password login.
      const exchange = await fetch("/api/portal/auth/cookie-exchange", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({
          access_token: session.portal_access_token,
          refresh_token: session.portal_refresh_token,
        }),
      });
      if (!exchange.ok) {
        setError(
          "We couldn't complete your sign-in. Please try again, or use email / password."
        );
        return;
      }

      // Honour ?redirect=… so social-login Web Chat handoff matches the
      // password-login flow. No ?token= — the cookie is shared cross-port.
      const target = safeRedirectTarget(params?.get("redirect") ?? null);
      if (target) {
        window.location.href = target;
        return;
      }
      router.replace("/dashboard");
    })();
  }, [router, params]);

  if (error) {
    return (
      <main
        className="min-h-screen flex items-center justify-center px-4 py-10"
        style={{ background: "var(--bg-base)" }}
      >
        <div className="flex flex-col items-center text-center max-w-sm">
          <div className="mb-4">
            <Logo variant="dark" size="md" />
          </div>
          <div
            className="w-11 h-11 rounded-full flex items-center justify-center mb-3"
            style={{ background: "rgba(220,38,38,0.1)", color: "var(--accent-danger)" }}
          >
            <AlertCircle size={22} />
          </div>
          <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
            {error}
          </p>
          <a
            href="/login"
            className="mt-4 inline-block font-medium hover:underline"
            style={{ color: "var(--accent-primary)" }}
          >
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  return (
    <main
      className="min-h-screen flex flex-col items-center justify-center px-4 py-10"
      style={{ background: "var(--bg-base)" }}
    >
      <Logo variant="dark" size="md" />
      <Spinner size={28} className="mt-6" />
      <p className="mt-3 text-sm" style={{ color: "var(--text-secondary)" }}>
        Signing you in…
      </p>
    </main>
  );
}
