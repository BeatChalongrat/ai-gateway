"use client";

/**
 * /forgot-password — enter email, receive reset link.
 *
 * Minimal UI so Beat's team can self-serve resets without an
 * admin. The backend always returns {"ok": true} regardless of
 * whether the email exists (no user-existence oracle); the page
 * therefore shows a constant "check your email" success message
 * without confirming whether the address is registered.
 */

import Link from "next/link";
import { CSSProperties, FormEvent, useState } from "react";
import { ArrowRight } from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
// Brand styles (.auth-root, .auth-form-card, .af-*) live in login.css.
// Without this import the page renders unstyled on a cold / email-link load.
import "../login/login.css";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post(
        "/portal/auth/password-reset/request",
        { email },
        { auth: false }
      );
      setSubmitted(true);
    } catch (err) {
      // The backend returns 200 for unknown emails too; a real error
      // here means something structural broke (500, network). Surface
      // a generic message so we don't leak endpoint details.
      if (err instanceof PortalApiError) {
        setError(err.message);
      } else {
        setError(
          "Couldn't reach the server right now. Please try again."
        );
      }
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="auth-root" style={authShellStyle}>
        <Link href="/login" className="auth-back-home">
          <span aria-hidden="true">←</span> Back to sign in
        </Link>
        <div className="auth-form-card">
          <div className="af-eyebrow af-anim d1">Check your email</div>
          <h1 className="af-title af-anim d1">Password reset requested</h1>
          <p className="af-desc af-anim d2">
            If an account exists for <strong>{email}</strong>, we've
            sent a password-reset link. Follow the link in the email
            to choose a new password. The link expires in 1 hour.
          </p>
          <p className="af-desc af-anim d2" style={{ marginTop: 8 }}>
            Didn't get the email? Check your spam folder, or return to{" "}
            <Link href="/login">sign in</Link> to try again.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-root" style={authShellStyle}>
      <Link href="/login" className="auth-back-home">
        <span aria-hidden="true">←</span> Back to sign in
      </Link>
      <div className="auth-form-card">
        <div className="af-eyebrow af-anim d1">Account recovery</div>
        <h1 className="af-title af-anim d1">Reset your password</h1>
        <p className="af-desc af-anim d2">
          Enter the email address on your SRAN account. We'll send a
          link you can use to choose a new password.
        </p>

        {error ? (
          <div className="af-error af-anim" role="alert">
            {error}
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="af-form af-anim d3">
          <div className="af-field">
            <label className="af-label" htmlFor="forgot-email">
              Email
            </label>
            <input
              id="forgot-email"
              type="email"
              className="af-input"
              placeholder="you@company.com"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <button type="submit" className="af-submit" disabled={loading}>
            {loading ? "Sending…" : "Send reset link"}
            {!loading ? (
              <span className="arrow" aria-hidden="true">
                <ArrowRight size={16} />
              </span>
            ) : null}
          </button>
        </form>

        <p className="af-bottom-link af-anim d4" style={{ marginTop: 24 }}>
          Remember your password? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}

// Centered single-card shell (these recovery pages have no split visual
// panel, so .auth-root alone doesn't center the .auth-form-card).
const authShellStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  minHeight: "100vh",
  padding: 24,
};
