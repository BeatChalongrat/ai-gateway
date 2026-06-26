"use client";

/**
 * /reset-password?token=... — set a new password given a valid
 * token from the reset email.
 *
 * Shows the new-password + confirm form. On success, redirect to
 * /login with a flash hint. Token validation happens server-side;
 * this page just forwards the request.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CSSProperties, FormEvent, Suspense, useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
// Brand styles (.auth-root, .auth-form-card, .af-*) live in login.css. This
// page is always entered cold via an email link, so without the import it
// renders unstyled.
import "../login/login.css";

// useSearchParams() requires a Suspense boundary so Next can prerender
// the static shell without bailing the whole route during build.
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}

function ResetPasswordInner() {
  const router = useRouter();
  const params = useSearchParams();
  const token = useMemo(() => params.get("token") || "", [params]);

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!token) {
      setError("Reset link is missing or invalid.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      setError(
        "Password must contain at least one letter and one number."
      );
      return;
    }
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setLoading(true);
    try {
      await api.post(
        "/portal/auth/password-reset/confirm",
        { token, new_password: password },
        { auth: false }
      );
      // Success — token is consumed, redirect to login. Old sessions
      // are already invalidated server-side via the token_version
      // bump.
      router.push("/login?reset=success");
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
    <div className="auth-root" style={authShellStyle}>
      <Link href="/login" className="auth-back-home">
        <span aria-hidden="true">←</span> Back to sign in
      </Link>
      <div className="auth-form-card">
        <div className="af-eyebrow af-anim d1">Account recovery</div>
        <h1 className="af-title af-anim d1">Choose a new password</h1>
        <p className="af-desc af-anim d2">
          Enter your new password below. Minimum 8 characters with at
          least one letter and one number.
        </p>

        {!token ? (
          <div className="af-error af-anim" role="alert">
            Reset link is missing a token. Request a new reset link
            from the <Link href="/forgot-password">forgot-password</Link>{" "}
            page.
          </div>
        ) : null}

        {error ? (
          <div className="af-error af-anim" role="alert">
            {error}
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="af-form af-anim d3">
          <div className="af-field">
            <label className="af-label" htmlFor="reset-pwd">
              New password
            </label>
            <div className="af-input-wrap">
              <input
                id="reset-pwd"
                type={showPwd ? "text" : "password"}
                className="af-input with-toggle"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="af-toggle"
                onClick={() => setShowPwd((v) => !v)}
                aria-label={showPwd ? "Hide password" : "Show password"}
              >
                {showPwd ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <div className="af-field">
            <label className="af-label" htmlFor="reset-confirm">
              Confirm new password
            </label>
            <input
              id="reset-confirm"
              type={showPwd ? "text" : "password"}
              className="af-input"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>

          <button
            type="submit"
            className="af-submit"
            disabled={loading || !token}
          >
            {loading ? "Updating…" : "Set new password"}
            {!loading ? (
              <span className="arrow" aria-hidden="true">
                <ArrowRight size={16} />
              </span>
            ) : null}
          </button>
        </form>
      </div>
    </div>
  );
}

// Centered single-card shell (no split visual panel on this page, so
// .auth-root alone doesn't center the .auth-form-card).
const authShellStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  minHeight: "100vh",
  padding: 24,
};
