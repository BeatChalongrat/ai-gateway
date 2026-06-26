"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";

export default function LoginPage() {
  const router = useRouter();
  // The backend session login accepts either a username (e.g. "admin")
  // or an email address. The JSON field name stays `email` for wire-
  // compat; the label/input type below reflect the real accepted shape.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
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
        if (res.status === 429) setError("Too many attempts. Try again later.");
        else setError("Invalid credentials");
        setLoading(false);
        return;
      }
      // Backend set admin_session + csrf_token cookies. Navigate home.
      router.push("/");
    } catch {
      setError("Unable to reach the backend. Try again.");
      setLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.65rem 0.875rem",
    background: "var(--bg-surface)",
    border: "1px solid var(--border-default)",
    borderRadius: "7px",
    color: "var(--text-primary)",
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
    color: "var(--text-secondary)",
    marginBottom: "0.45rem",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
  };

  return (
    <div style={{
      minHeight: "100vh",
      // Theme-token backdrop so the login respects the user's marked theme
      // (navy-ish in dark, light in light) — the grid + glow overlays below
      // keep a subtle brand texture on both. Sidebar/brand stays navy regardless.
      background: "var(--bg-base)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "Inter, -apple-system, sans-serif",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* Keyboard-focus ring on the inputs (the inline onFocus only swaps the
          border colour; this adds a visible focus-visible outline). */}
      <style>{`
        .login-input:focus-visible {
          outline: 2px solid var(--accent-primary);
          outline-offset: 2px;
        }
      `}</style>

      {/* Grid pattern */}
      <div style={{
        position: "absolute", inset: 0, opacity: 0.03,
        backgroundImage: "linear-gradient(rgba(100,160,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(100,160,255,1) 1px, transparent 1px)",
        backgroundSize: "40px 40px",
        pointerEvents: "none",
      }} />

      {/* Glow */}
      <div style={{
        position: "absolute", top: "-100px", right: "-100px",
        width: "400px", height: "400px",
        background: "radial-gradient(circle, rgba(60,100,220,0.12) 0%, transparent 70%)",
        pointerEvents: "none",
      }} />

      <div style={{ width: "100%", maxWidth: "420px", padding: "0 1.25rem", position: "relative", zIndex: 1, margin: "0 auto" }}>
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
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--text-primary)", margin: "0 0 0.35rem", letterSpacing: "0.05em" }}>
            SRAN AI Gateway
          </h1>
          <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", margin: 0, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Admin Console
          </p>
        </div>

        {/* Form card */}
        <div style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
          borderRadius: "14px",
          padding: "2rem 2rem",
          backdropFilter: "blur(8px)",
          width: "100%",
          boxSizing: "border-box" as const,
        }}>
          <form onSubmit={handleSubmit} style={{ width: "100%" }}>
            <div style={{ marginBottom: "1.25rem" }}>
              <label style={labelStyle}>Username or email</label>
              <input
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin or you@example.com"
                autoComplete="username"
                required
                aria-invalid={error ? true : undefined}
                className="login-input"
                style={inputStyle}
                onFocus={(e) => { e.target.style.borderColor = "var(--accent-primary)"; }}
                onBlur={(e) => { e.target.style.borderColor = "var(--border-default)"; }}
              />
            </div>

            <div style={{ marginBottom: "1.5rem" }}>
              <label style={labelStyle}>Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                autoComplete="current-password"
                required
                aria-invalid={error ? true : undefined}
                className="login-input"
                style={inputStyle}
                onFocus={(e) => { e.target.style.borderColor = "var(--accent-primary)"; }}
                onBlur={(e) => { e.target.style.borderColor = "var(--border-default)"; }}
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
                marginBottom: "1.25rem",
              }}>
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                width: "100%",
                padding: "0.7rem",
                background: loading ? "var(--bg-elevated)" : "var(--color-sran-700)",
                border: "1px solid var(--color-sran-800)",
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
              {loading ? "Authenticating..." : "Sign In"}
            </button>
          </form>
        </div>

        <p style={{ textAlign: "center", fontSize: "0.65rem", color: "var(--text-muted)", marginTop: "1.5rem", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          SRAN AI Gateway v0.1.0
        </p>
      </div>
    </div>
  );
}
