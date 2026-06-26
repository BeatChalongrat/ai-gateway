"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
import type { AuthResponse } from "@/lib/types";
import { SocialButtons, OrDivider } from "@/components/SocialButtons";

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

interface SignupFormProps {
  // When the user arrived via /invite/{code}, the landing page passes
  // the code down so the form pre-fills it + hides the "Have an invite
  // code?" toggle. Optional — unpressed signup still works.
  presetInviteCode?: string;
  inviteContext?: {
    tenantName: string;
    role: string;
    inviterName: string | null;
  } | null;
}

export default function SignupForm({
  presetInviteCode,
  inviteContext,
}: SignupFormProps = {}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [inviteCode, setInviteCode] = useState(presetInviteCode ?? "");
  // Unchecked by default — pre-ticked consent is a recognized anti-pattern
  // (GDPR/UX); the user must explicitly accept (locked decision).
  const [tos, setTos] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const locked = Boolean(presetInviteCode);

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
      // Signup auto-logs-in via HttpOnly cookies (backend Set-Cookie);
      // the body carries only user info.
      await api.post<AuthResponse>(
        "/portal/auth/signup",
        {
          email,
          password,
          display_name: name.trim() || undefined,
          invite_code: inviteCode.trim() || undefined,
        },
        { auth: false }
      );
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
          {inviteContext ? (
            <>
              {/* Default white-on-gradient pill — a custom light-lavender
                  override read low-contrast on the violet→cyan background. */}
              <div className="sv-form-eyebrow">Team invite</div>
              <h1>Join {inviteContext.tenantName}</h1>
              <p>
                {inviteContext.inviterName
                  ? `${inviteContext.inviterName} invited you to join as a ${inviteContext.role}.`
                  : `You were invited to join as a ${inviteContext.role}.`}{" "}
                Create your account below.
              </p>
            </>
          ) : (
            <>
              <div className="sv-form-eyebrow">Free forever tier</div>
              <h1>Create your account</h1>
              <p>No credit card. 5 minutes to first API call.</p>
            </>
          )}
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
            {confirm ? (
              <span
                className={`sv-match-hint ${
                  password === confirm ? "ok" : "bad"
                }`}
                aria-live="polite"
              >
                {password === confirm
                  ? "Passwords match"
                  : "Passwords don't match"}
              </span>
            ) : null}
          </div>

          {locked ? null : (
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
          )}

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
            {!loading ? <span className="sv-arrow" aria-hidden="true"><ArrowRight size={16} /></span> : null}
          </button>
        </form>

        {/* Shared env-gated OAuth block (mirrors the login surface). When
            NEXT_PUBLIC_OAUTH_ENABLED !== "true" it renders a "coming soon"
            notice instead of dead disabled buttons; when enabled it wires
            real next-auth signIn handlers. */}
        <div className="anim d3">
          <OrDivider label="or continue with" />
          <SocialButtons />
        </div>

        <p className="sv-bottom-link anim d3">
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </section>
  );
}
