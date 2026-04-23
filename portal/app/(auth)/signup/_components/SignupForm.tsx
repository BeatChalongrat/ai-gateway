"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

import { api, PortalApiError } from "@/lib/api";
import { setTokens } from "@/lib/auth";
import type { AuthResponse } from "@/lib/types";

type Strength = 0 | 1 | 2 | 3;

function computeStrength(pwd: string): Strength {
  if (!pwd) return 0;
  let score = 0;
  if (pwd.length >= 8) score++;
  if (/[A-Z]/.test(pwd) && /[a-z]/.test(pwd)) score++;
  if (/\d/.test(pwd) && /[^A-Za-z0-9]/.test(pwd)) score++;
  return Math.min(score, 3) as Strength;
}

const STRENGTH_LABEL: Record<Strength, { text: string; cls: string }> = {
  0: { text: "", cls: "" },
  1: { text: "Weak", cls: "s1" },
  2: { text: "Okay", cls: "s2" },
  3: { text: "Strong", cls: "s3" },
};

export default function SignupForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [tos, setTos] = useState(true);
  const [showPwd, setShowPwd] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const strength = useMemo(() => computeStrength(password), [password]);
  const meta = STRENGTH_LABEL[strength];

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (!tos) {
      setError("Please accept the Terms of Service to continue.");
      return;
    }
    setLoading(true);
    try {
      const data = await api.post<AuthResponse>(
        "/portal/auth/signup",
        {
          email,
          password,
          display_name: name.trim() || undefined,
          invite_code: inviteCode.trim() || undefined,
        },
        { auth: false }
      );
      setTokens(data.access_token, data.refresh_token);
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
    <section className="sv-form-side">
      <div className="sv-form-wrap">
        <Link href="/" className="auth-back-home" aria-label="Back to home">
          <span aria-hidden="true">←</span> Back to home
        </Link>
        <div className="sv-form-header anim d1">
          <div className="sv-form-eyebrow">Free forever tier</div>
          <h1>Create your account</h1>
          <p>No credit card. 5 minutes to first API call.</p>
        </div>

        <form className="sv-form anim d2" onSubmit={handleSubmit} noValidate>
          <div className="sv-field">
            <label className="sv-label" htmlFor="su-name">Display name</label>
            <input
              id="su-name"
              type="text"
              className="sv-input"
              autoComplete="name"
              placeholder="Your name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="sv-field">
            <label className="sv-label" htmlFor="su-email">Work email</label>
            <input
              id="su-email"
              type="email"
              className="sv-input"
              autoComplete="email"
              placeholder="you@company.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="sv-field">
            <label className="sv-label" htmlFor="su-pwd">Password</label>
            <div className="sv-input-wrap">
              <input
                id="su-pwd"
                type={showPwd ? "text" : "password"}
                className="sv-input with-toggle"
                autoComplete="new-password"
                placeholder="At least 8 characters"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="sv-toggle"
                onClick={() => setShowPwd((v) => !v)}
                aria-label={showPwd ? "Hide password" : "Show password"}
              >
                {showPwd ? "Hide" : "Show"}
              </button>
            </div>
            <div className="sv-strength" aria-live="polite">
              <div className="sv-strength-bars" aria-hidden="true">
                <span className={`sv-bar${strength >= 1 ? ` ${meta.cls}` : ""}`} />
                <span className={`sv-bar${strength >= 2 ? ` ${meta.cls}` : ""}`} />
                <span className={`sv-bar${strength >= 3 ? ` ${meta.cls}` : ""}`} />
              </div>
              <span className={`sv-strength-label ${meta.cls}`}>{meta.text}</span>
            </div>
          </div>

          <div className="sv-field">
            <label className="sv-label" htmlFor="su-confirm">Confirm password</label>
            <div className="sv-input-wrap">
              <input
                id="su-confirm"
                type={showConfirm ? "text" : "password"}
                className="sv-input with-toggle"
                autoComplete="new-password"
                placeholder="Repeat password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
              <button
                type="button"
                className="sv-toggle"
                onClick={() => setShowConfirm((v) => !v)}
                aria-label={showConfirm ? "Hide password" : "Show password"}
              >
                {showConfirm ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <div className="sv-field">
            {!showInvite ? (
              <button
                type="button"
                className="sv-invite-toggle"
                onClick={() => setShowInvite(true)}
              >
                Have an invite code? Click to enter it.
              </button>
            ) : (
              <>
                <label className="sv-label" htmlFor="su-invite">
                  Invite code (optional)
                </label>
                <input
                  id="su-invite"
                  type="text"
                  className="sv-input"
                  autoComplete="off"
                  placeholder="Paste the code your team owner shared"
                  value={inviteCode}
                  onChange={(e) => setInviteCode(e.target.value)}
                />
                <p className="sv-invite-hint">
                  Joining an existing team with this code. Leave empty
                  to create your own team.
                </p>
              </>
            )}
          </div>

          <label className="sv-tos">
            <input
              type="checkbox"
              checked={tos}
              onChange={(e) => setTos(e.target.checked)}
            />
            <span>
              I agree to the{" "}
              <a href="/terms" target="_blank" rel="noopener" className="sv-tos-link">
                Terms of Service
              </a>{" "}
              and{" "}
              <a href="/privacy" target="_blank" rel="noopener" className="sv-tos-link">
                Privacy Policy
              </a>
              .
            </span>
          </label>

          {error ? (
            <div className="sv-error" role="alert">{error}</div>
          ) : null}

          <button type="submit" className="sv-submit" disabled={loading}>
            {loading ? "Creating account…" : "Create account"}
            {!loading ? <span className="sv-arrow" aria-hidden="true">→</span> : null}
          </button>
        </form>

        <div className="sv-divider anim d3">
          <span>or continue with</span>
        </div>

        <div className="sv-socials anim d3">
          <button type="button" className="sv-social" disabled aria-disabled="true">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#4285F4" d="M21.6 12.227c0-.7-.063-1.376-.18-2.027H12v3.836h5.4a4.62 4.62 0 01-2.005 3.03v2.515h3.246c1.9-1.748 2.96-4.325 2.96-7.354z" />
              <path fill="#34A853" d="M12 22c2.7 0 4.965-.895 6.62-2.42l-3.246-2.515c-.9.6-2.05.96-3.374.96-2.594 0-4.79-1.752-5.575-4.107H3.07v2.582A9.997 9.997 0 0012 22z" />
              <path fill="#FBBC05" d="M6.425 13.918A6.01 6.01 0 016.105 12c0-.665.115-1.31.32-1.918V7.5H3.07A9.997 9.997 0 002 12c0 1.614.387 3.14 1.07 4.5l3.355-2.582z" />
              <path fill="#EA4335" d="M12 5.975c1.466 0 2.78.504 3.815 1.493l2.86-2.86C16.96 2.99 14.695 2 12 2 8.07 2 4.673 4.255 3.07 7.5l3.355 2.582C7.21 7.727 9.406 5.975 12 5.975z" />
            </svg>
            Google
          </button>
          <button type="button" className="sv-social" disabled aria-disabled="true">
            <svg viewBox="0 0 24 24" fill="#1A1F36" aria-hidden="true">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56v-1.97c-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.27-1.69-1.27-1.69-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.68 1.25 3.34.95.1-.74.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.18-3.08-.12-.29-.51-1.46.11-3.05 0 0 .96-.31 3.16 1.18a10.95 10.95 0 015.75 0c2.2-1.49 3.16-1.18 3.16-1.18.62 1.59.23 2.76.11 3.05.74.8 1.18 1.82 1.18 3.08 0 4.42-2.7 5.39-5.27 5.68.41.36.78 1.07.78 2.16v3.2c0 .31.21.67.8.56C20.21 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
            </svg>
            GitHub
          </button>
        </div>

        <p className="sv-bottom-link anim d3">
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </section>
  );
}
