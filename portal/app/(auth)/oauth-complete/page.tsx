"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { getSession } from "next-auth/react";
import { useEffect, useState } from "react";

import { Spinner } from "@/components/Spinner";
import { setTokens } from "@/lib/auth";

// Landing page for a successful NextAuth OAuth sign-in. Reads the
// portal tokens that the signIn/jwt/session callbacks stashed on the
// NextAuth session, mirrors them to localStorage + cookie (the same
// place password login writes), then forwards to ``?redirect=`` (if
// safe) or /dashboard. From this point on the rest of the app is
// agnostic to where the tokens came from.

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

export default function OAuthCompletePage() {
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
      setTokens(session.portal_access_token, session.portal_refresh_token);

      // Honour ?redirect=… so social-login Web Chat handoff matches
      // the password-login flow (LoginForm.tsx does the same dance).
      const target = safeRedirectTarget(params?.get("redirect") ?? null);
      if (target) {
        const webChatOrigin = new URL(
          process.env.NEXT_PUBLIC_WEB_CHAT_URL || "http://localhost:3002",
        ).origin;
        let final = target;
        try {
          const tu = new URL(target, window.location.origin);
          if (tu.origin === webChatOrigin) {
            tu.searchParams.set("token", session.portal_access_token);
            final = tu.toString();
          }
        } catch {
          /* leave target as-is */
        }
        window.location.href = final;
        return;
      }
      router.replace("/dashboard");
    })();
  }, [router, params]);

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center px-4 py-10 bg-bg-page">
        <div className="text-center">
          <p className="text-brand-red text-sm">{error}</p>
          <a
            href="/login"
            className="mt-4 inline-block text-brand-navy font-medium hover:underline"
          >
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 py-10 bg-bg-page text-gray-500">
      <Spinner size={28} />
      <p className="mt-3 text-sm">Signing you in…</p>
    </main>
  );
}
