"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import { FormEvent, useState } from "react";
import { ArrowRight } from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
import type { AuthResponse } from "@/lib/types";

// Phase 1 — Google-first sign-in. When the flag is on, Google is the
// primary (and only) visible way in and the email+password form is
// HIDDEN (not removed — the server-side PASSWORD_LOGIN_ENABLED flag is
// what actually closes that surface in Phase 3; this is just UI).
// GitHub deliberately not rendered (finding L2: Google-only for users).
// NEXT_PUBLIC_* is baked at BUILD time — flipping requires a rebuild.
const OAUTH_ENABLED = process.env.NEXT_PUBLIC_OAUTH_ENABLED === "true";

// Allowlist for ``?redirect=`` targets. Web Chat is the only
// cross-origin destination today; everything else must be a
// relative path on the Portal. An attacker who can phish a
// crafted /login?redirect=https://evil.example.com URL would
// otherwise harvest the user's just-typed credentials → cookie →
// then bounce them off-site mid-login. Same-origin + the
// configured web-chat origin only.
function safeRedirectTarget(raw: string | null): string | null {
  if (!raw) return null;
  // Relative paths starting with "/" but not "//" or "/\\" are safe.
  if (raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\")) {
    return raw;
  }
  try {
    const u = new URL(raw, window.location.origin);
    const allowed = [
      window.location.origin,
      process.env.NEXT_PUBLIC_WEB_CHAT_URL || "http://localhost:3002",
    ].map((o) => new URL(o).origin);
    if (allowed.includes(u.origin)) {
      return u.toString();
    }
  } catch {
    /* fall through */
  }
  return null;
}

export default function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [socialLoading, setSocialLoading] = useState<"google" | "github" | null>(
    null,
  );

  // Set by the reset-password flow on successful completion
  // (router.push("/login?reset=success")). Visual-only flash.
  const resetSuccess = params?.get("reset") === "success";

  function handleSocial(provider: "google" | "github") {
    setSocialLoading(provider);
    setError(null);
    // After NextAuth completes the dance + backend verification,
    // /oauth-complete reads the session and mirrors the portal tokens
    // into localStorage + the marker cookie, then forwards to
    // /dashboard. We pass the optional ?redirect= target through so a
    // user who arrived via Web Chat's "Sign in" lands back there
    // after sign-in completes.
    const redirect = params?.get("redirect");
    const callbackUrl = redirect
      ? `/oauth-complete?redirect=${encodeURIComponent(redirect)}`
      : "/oauth-complete";
    // signIn returns a Promise but we don't await it — NextAuth
    // performs a full-page redirect to the provider, so anything
    // after this point would be unmounted before resolving.
    void signIn(provider, { callbackUrl });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // Login sets the HttpOnly auth cookies (backend Set-Cookie); the
      // response body carries only user info now.
      await api.post<AuthResponse>(
        "/portal/auth/login",
        { email, password },
        { auth: false }
      );

      // Honor ?redirect=… for the Web Chat cross-port handoff. No more
      // ?token= in the URL — the portal cookie is host-only on localhost
      // (shared across ports) / set on the shared parent domain in prod,
      // so Web Chat already has the session when it loads.
      const target = safeRedirectTarget(params?.get("redirect") ?? null);
      if (target) {
        window.location.href = target;
        return;
      }
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof PortalApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
      setLoading(false);
    }
  }

  return (
    <>
      <Link href="/" className="auth-back-home" aria-label="Back to home">
        <span aria-hidden="true">←</span> Back to home
      </Link>
      <div className="auth-form-card">
        <div className="af-eyebrow af-anim d1">Sign in</div>
        <h1 className="af-title af-anim d1">Welcome back</h1>
        <p className="af-desc af-anim d2">
          {OAUTH_ENABLED
            ? "Sign in with your company Google account."
            : "Enter your credentials to access your SRAN dashboard."}
        </p>

        {resetSuccess ? (
          <div className="af-success af-anim" role="status">
            Your password was reset — sign in with your new password.
          </div>
        ) : null}

        {error ? (
          <div className="af-error af-anim" role="alert">
            {error}
          </div>
        ) : null}

        {OAUTH_ENABLED ? (
          <div className="af-form af-anim d3">
            <button
              type="button"
              className="af-submit"
              onClick={() => handleSocial("google")}
              disabled={socialLoading !== null}
              aria-label="Sign in with Google"
            >
              <svg
                viewBox="0 0 24 24"
                aria-hidden="true"
                width="18"
                height="18"
                style={{ marginRight: 8, flexShrink: 0 }}
              >
                <path fill="#4285F4" d="M21.6 12.227c0-.7-.063-1.376-.18-2.027H12v3.836h5.4a4.62 4.62 0 01-2.005 3.03v2.515h3.246c1.9-1.748 2.96-4.325 2.96-7.354z" />
                <path fill="#34A853" d="M12 22c2.7 0 4.965-.895 6.62-2.42l-3.246-2.515c-.9.6-2.05.96-3.374.96-2.594 0-4.79-1.752-5.575-4.107H3.07v2.582A9.997 9.997 0 0012 22z" />
                <path fill="#FBBC05" d="M6.425 13.918A6.01 6.01 0 016.105 12c0-.665.115-1.31.32-1.918V7.5H3.07A9.997 9.997 0 002 12c0 1.614.387 3.14 1.07 4.5l3.355-2.582z" />
                <path fill="#EA4335" d="M12 5.975c1.466 0 2.78.504 3.815 1.493l2.86-2.86C16.96 2.99 14.695 2 12 2 8.07 2 4.673 4.255 3.07 7.5l3.355 2.582C7.21 7.727 9.406 5.975 12 5.975z" />
              </svg>
              {socialLoading === "google"
                ? "Redirecting to Google…"
                : "Continue with Google"}
            </button>
          </div>
        ) : null}

        {/* Email+password form — HIDDEN (not removed) while Google SSO is
            on. Server-side enforcement is PASSWORD_LOGIN_ENABLED (403 on
            /portal/auth/login); this conditional is presentation only. */}
        {OAUTH_ENABLED ? null : (
        <form onSubmit={handleSubmit} className="af-form af-anim d3">
          <div className="af-field">
            <label className="af-label" htmlFor="login-email">
              Email
            </label>
            <input
              id="login-email"
              type="email"
              className="af-input"
              placeholder="you@company.com"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="af-field">
            <div className="af-label-row">
              <label className="af-label" htmlFor="login-password">
                Password
              </label>
              <Link href="/forgot-password" className="af-link">
                Forgot password?
              </Link>
            </div>
            <div className="af-input-wrap">
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                className="af-input with-toggle"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="af-toggle"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <button type="submit" className="af-submit" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
            {!loading ? <span className="arrow" aria-hidden="true"><ArrowRight size={16} /></span> : null}
          </button>
        </form>
        )}

        <p className="af-bottom-link af-anim d5">
          {OAUTH_ENABLED ? (
            <>
              Don&apos;t have an account yet?{" "}
              <Link href="/signup">Get started</Link>
            </>
          ) : (
            <>
              Don&apos;t have an account?{" "}
              <Link href="/signup">Sign up free</Link>
            </>
          )}
        </p>
      </div>
    </>
  );
}
