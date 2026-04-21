"use client";

import { useRouter } from "next/navigation";
import { getSession } from "next-auth/react";
import { useEffect, useState } from "react";

import { Spinner } from "@/components/Spinner";
import { setTokens } from "@/lib/auth";

// Landing page for a successful NextAuth OAuth sign-in. Reads the
// portal tokens that the signIn/jwt/session callbacks stashed on the
// NextAuth session, mirrors them to localStorage + cookie (the same
// place password login writes), then bounces to /dashboard. From this
// point on the rest of the app is agnostic to where the tokens came
// from.

interface PortalSession {
  portal_access_token?: string;
  portal_refresh_token?: string;
}

export default function OAuthCompletePage() {
  const router = useRouter();
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
      router.replace("/dashboard");
    })();
  }, [router]);

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
