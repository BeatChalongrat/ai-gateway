"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AlertCircle } from "lucide-react";

import { Logo } from "@/components/Logo";
import { Spinner } from "@/components/Spinner";

// Landing page for a successful NextAuth OAuth sign-in. Phase 11 E3:
// the portal tokens never touch client JS any more. This page fires a
// single tokenless POST to the server-side cookie-bridge route
// (/api/oauth/cookie-bridge), which decodes the HttpOnly NextAuth JWT
// with getToken(), exchanges the tokens with the backend cookie-
// exchange endpoint, and relays the HttpOnly portal cookies onto the
// response — same place password login lands. Then forwards to
// ``?redirect=`` (if safe) or /dashboard.

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

// Centered branded card shell with a one-shot page-enter (fade + lift).
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main
      className="min-h-screen flex items-center justify-center px-4 py-10"
      style={{ background: "var(--bg-base)" }}
    >
      <div
        className="page-enter"
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
          borderRadius: 16,
          boxShadow: "var(--shadow-md)",
          padding: "32px 28px",
          width: "100%",
          maxWidth: 380,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          textAlign: "center",
        }}
      >
        {children}
      </div>
    </main>
  );
}

function LoadingView() {
  return (
    <Shell>
      <Logo variant="dark" size="md" />
      <Spinner size={28} className="mt-6" />
      <p className="mt-3 text-sm" style={{ color: "var(--text-secondary)" }}>
        Securely signing you in…
      </p>
    </Shell>
  );
}

function OAuthCompleteFallback() {
  return <LoadingView />;
}

function OAuthCompleteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      // Server-side bridge: no tokens in the request, none in the
      // response body — the NextAuth JWT cookie authenticates us and
      // the portal cookies come back as Set-Cookie headers.
      let ok = false;
      try {
        const exchange = await fetch("/api/oauth/cookie-bridge", {
          method: "POST",
          credentials: "include",
        });
        ok = exchange.ok;
      } catch {
        ok = false;
      }
      if (!ok) {
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
      <Shell>
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
          style={{ color: "var(--accent-text)" }}
        >
          Back to sign in
        </a>
      </Shell>
    );
  }

  return <LoadingView />;
}
