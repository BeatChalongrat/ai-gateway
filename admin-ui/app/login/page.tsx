"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { useT, type DictKey } from "../../lib/i18n";
import { landingFor } from "../../lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

export default function LoginPage() {
  const router = useRouter();
  const t = useT();
  // The backend session login accepts either a username (e.g. "admin")
  // or an email address. The JSON field name stays `email` for wire-
  // compat; the label/input type below reflect the real accepted shape.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // The error is held as a dictionary KEY, not rendered text, so flipping
  // the language switcher re-labels a visible error instead of stranding
  // it in whichever locale was active when the request failed.
  const [error, setError] = useState<DictKey | "">("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/auth/session/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        if (res.status === 429) setError("dash.login.errorRateLimited");
        else setError("dash.login.errorInvalid");
        setLoading(false);
        return;
      }
      // Backend set admin_session + csrf_token cookies. AM9: each role lands on the first screen it
      // may use (an agent publisher on the Agent Market page, not a dashboard of errors).
      const body = await res.json().catch(() => null);
      router.push(landingFor(body?.user?.role));
    } catch {
      setError("dash.login.errorNetwork");
      setLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "14px 16px",
    // background/border/color come from the .login-input rules in
    // globals.css — an inline background/color here would beat the class
    // and break the Chrome-autofill override that lives there.
    borderRadius: "7px",
    fontSize: "0.875rem",
    outline: "none",
    boxSizing: "border-box",
    transition: "border-color 0.15s",
    fontFamily: "Inter, -apple-system, sans-serif",
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: "0.72rem",
    fontWeight: 600,
    color: "#8B96BA",
    marginBottom: "0.45rem",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
  };

  return (
    <div style={{
      minHeight: "100vh",
      // The login is always dark-navy regardless of the app's data-theme —
      // --bg-base/--text-primary are re-declared here (scoped) so the global
      // light/dark tokens in globals.css stay untouched for the rest of the
      // app. #0A1230 matches the dark theme's --bg-base (Dashboard et al).
      ["--bg-base" as string]: "#0A1230",
      ["--text-primary" as string]: "#E8E9ED",
      background: "var(--bg-base)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "Inter, -apple-system, sans-serif",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* Grid pattern */}
      <div style={{
        position: "absolute", inset: 0, opacity: 0.06,
        backgroundImage: "linear-gradient(rgba(100,160,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(100,160,255,1) 1px, transparent 1px)",
        backgroundSize: "40px 40px",
        pointerEvents: "none",
      }} />

      {/* Glow */}
      <div style={{
        position: "absolute", top: "-100px", right: "-100px",
        width: "400px", height: "400px",
        background: "radial-gradient(circle, rgba(60,100,220,0.08) 0%, transparent 70%)",
        pointerEvents: "none",
      }} />

      <div style={{ width: "100%", maxWidth: "400px", padding: "0 1.25rem", position: "relative", zIndex: 1, margin: "0 auto" }}>
        {/* Logo + Title */}
        <div style={{ textAlign: "center", marginBottom: "2rem" }}>
          <img
            src="/sran-logo.png"
            alt="SRAN"
            style={{
              width: "72px", height: "72px", borderRadius: "16px", objectFit: "cover" as const,
              display: "block", margin: "0 auto 1.25rem",
              boxShadow: "0 0 30px rgba(80,130,255,0.25), 0 8px 24px rgba(0,0,0,0.4)",
            }}
          />
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, color: "#E8E9ED", margin: "0 0 0.35rem", letterSpacing: "0.05em" }}>
            {t("app.title")}
          </h1>
          <p style={{ fontSize: "0.75rem", color: "rgba(160, 170, 200, 0.6)", margin: 0, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            {t("app.subtitle")}
          </p>
        </div>

        {/* Form card */}
        <div style={{
          background: "rgba(15, 20, 35, 0.4)",
          border: "1px solid rgba(100, 130, 255, 0.12)",
          borderRadius: "16px",
          padding: "40px 36px",
          backdropFilter: "blur(12px)",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.3)",
          width: "100%",
          boxSizing: "border-box" as const,
        }}>
          <form onSubmit={handleSubmit} style={{ width: "100%" }}>
            <div style={{ marginBottom: "24px" }}>
              <label style={labelStyle}>{t("dash.login.identifier")}</label>
              <input
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={t("dash.login.identifierPlaceholder")}
                autoComplete="username"
                required
                aria-invalid={error ? true : undefined}
                className="login-input"
                style={inputStyle}
              />
            </div>

            <div style={{ marginBottom: "24px" }}>
              <label style={labelStyle}>{t("dash.login.password")}</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("dash.login.passwordPlaceholder")}
                autoComplete="current-password"
                required
                aria-invalid={error ? true : undefined}
                className="login-input"
                style={inputStyle}
              />
            </div>

            {error && (
              <div style={{
                padding: "0.6rem 0.875rem",
                background: "var(--color-status-error-bg)",
                border: "1px solid var(--color-status-error)",
                borderRadius: "6px",
                color: "var(--color-status-error)",
                fontSize: "0.8rem",
                marginBottom: "24px",
              }}>
                {t(error)}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="login-submit"
              style={{
                width: "100%",
                padding: "14px",
                background: loading ? "rgba(30, 58, 95, 0.6)" : "#1E3A5F",
                border: "1px solid #254A78",
                borderRadius: "7px",
                color: "#ffffff",
                fontSize: "0.875rem",
                fontWeight: 600,
                cursor: loading ? "not-allowed" : "pointer",
                letterSpacing: "0.05em",
                transition: "all 0.15s",
                fontFamily: "Inter, -apple-system, sans-serif",
                boxSizing: "border-box" as const,
              }}
            >
              {loading ? t("dash.login.submitting") : t("dash.login.submit")}
            </button>
          </form>
        </div>

        <p style={{ textAlign: "center", fontSize: "0.65rem", color: "rgba(120, 130, 160, 0.4)", letterSpacing: "0.06em", textTransform: "uppercase", margin: "1.5rem 0 0" }}>
          SRAN AI Gateway v0.1.0
        </p>
      </div>
    </div>
  );
}
