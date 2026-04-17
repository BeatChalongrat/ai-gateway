"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { type DemoRole } from "../../lib/auth";

const DEMO_USERS: Record<string, { password: string; role: DemoRole; name: string }> = {
  admin: { password: "admin1234", role: "admin", name: "Super Admin" },
  manager: { password: "manager1234", role: "project_admin", name: "Project Admin" },
  viewer: { password: "viewer1234", role: "viewer", name: "Demo Viewer" },
};

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    await new Promise((r) => setTimeout(r, 400));

    const user = DEMO_USERS[username.toLowerCase()];
    if (!user || user.password !== password) {
      setError("Invalid username or password");
      setLoading(false);
      return;
    }

    const session = { role: user.role, name: user.name, loggedInAt: Date.now() };
    localStorage.setItem("sran_demo_session", JSON.stringify(session));
    document.cookie = `sran_demo_role=${user.role}; path=/; max-age=86400`;
    router.push("/");
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "0.65rem 0.875rem",
    background: "rgba(255,255,255,0.04)",
    border: "1px solid rgba(100,140,220,0.2)",
    borderRadius: "7px",
    color: "#e8f0ff",
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
    color: "rgba(160,185,230,0.75)",
    marginBottom: "0.45rem",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
  };

  return (
    <div style={{
      minHeight: "100vh",
      background: "linear-gradient(135deg, #08152e 0%, #0d1f40 50%, #0a1830 100%)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "Inter, -apple-system, sans-serif",
      position: "relative",
      overflow: "hidden",
    }}>
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
          <h1 style={{ fontSize: "1.5rem", fontWeight: 700, color: "#ffffff", margin: "0 0 0.35rem", letterSpacing: "0.05em" }}>
            SRAN AI Gateway
          </h1>
          <p style={{ fontSize: "0.75rem", color: "rgba(140,170,220,0.65)", margin: 0, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            Admin Console
          </p>
        </div>

        {/* Form card */}
        <div style={{
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(100,140,220,0.15)",
          borderRadius: "14px",
          padding: "2rem 2rem",
          backdropFilter: "blur(8px)",
          width: "100%",
          boxSizing: "border-box" as const,
        }}>
          <form onSubmit={handleSubmit} style={{ width: "100%" }}>
            <div style={{ marginBottom: "1.25rem" }}>
              <label style={labelStyle}>Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter username"
                autoComplete="username"
                style={inputStyle}
                onFocus={(e) => { e.target.style.borderColor = "rgba(100,160,255,0.5)"; }}
                onBlur={(e) => { e.target.style.borderColor = "rgba(100,140,220,0.2)"; }}
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
                style={inputStyle}
                onFocus={(e) => { e.target.style.borderColor = "rgba(100,160,255,0.5)"; }}
                onBlur={(e) => { e.target.style.borderColor = "rgba(100,140,220,0.2)"; }}
              />
            </div>

            {error && (
              <div style={{
                padding: "0.6rem 0.875rem",
                background: "rgba(220,50,50,0.1)",
                border: "1px solid rgba(220,50,50,0.25)",
                borderRadius: "6px",
                color: "rgba(255,130,130,0.9)",
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
                background: loading ? "rgba(60,100,200,0.4)" : "linear-gradient(135deg, #2d5bc4 0%, #1e3d8f 100%)",
                border: "1px solid rgba(100,150,255,0.3)",
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

        <p style={{ textAlign: "center", fontSize: "0.68rem", color: "rgba(100,130,180,0.5)", marginTop: "1.5rem", letterSpacing: "0.04em" }}>
          Demo: admin / admin1234
        </p>
        <p style={{ textAlign: "center", fontSize: "0.65rem", color: "rgba(80,110,160,0.4)", marginTop: "0.5rem", letterSpacing: "0.06em", textTransform: "uppercase" }}>
          SRAN AI Gateway v0.1.0
        </p>
      </div>
    </div>
  );
}
