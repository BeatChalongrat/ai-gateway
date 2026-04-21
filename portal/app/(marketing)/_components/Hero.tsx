import Link from "next/link";

import { FlowDiagram } from "@/components/landing/FlowDiagram";
import { HeroBackground } from "@/components/landing/HeroBackground";

export function Hero() {
  return (
    <section className="relative overflow-hidden" style={{ padding: "80px 0 100px" }}>
      <HeroBackground />
      <div className="container relative" style={{ zIndex: 1 }}>
        <div className="text-center mx-auto anim d1" style={{ maxWidth: 780 }}>
          <div
            className="inline-flex items-center gap-2 anim d1"
            style={{
              padding: "6px 14px 6px 10px",
              borderRadius: 999,
              background: "rgba(255,255,255,0.7)",
              border: "1px solid rgba(203,213,225,0.6)",
              fontSize: 13,
              color: "#334155",
              backdropFilter: "blur(8px)",
              WebkitBackdropFilter: "blur(8px)",
              marginBottom: 28,
              boxShadow: "0 4px 16px -6px rgba(15,23,42,0.08)",
            }}
          >
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#10b981",
                boxShadow: "0 0 10px #10b981, 0 0 2px #10b981",
              }}
            />
            <strong style={{ color: "#0f172a", fontWeight: 600 }}>New:</strong>
            <span>Semantic routing cuts AI costs by 38%</span>
          </div>

          <h1
            className="anim d2"
            style={{
              fontSize: "clamp(40px, 6vw, 72px)",
              lineHeight: 1.08,
              letterSpacing: "-0.035em",
              fontWeight: 600,
              marginBottom: 20,
              color: "#0f172a",
            }}
          >
            AI infrastructure,
            <br />
            <span
              className="animate-text-shine"
              style={{
                backgroundImage:
                  "linear-gradient(90deg, #0f172a 0%, #6366f1 45%, #8b5cf6 65%, #0f172a 100%)",
                backgroundSize: "200% 100%",
                WebkitBackgroundClip: "text",
                backgroundClip: "text",
                color: "transparent",
                display: "inline-block",
              }}
            >
              simplified.
            </span>
          </h1>

          <p
            className="anim d3"
            style={{
              fontSize: 19,
              lineHeight: 1.55,
              color: "#475569",
              maxWidth: 620,
              margin: "0 auto 36px",
              fontWeight: 400,
            }}
          >
            One endpoint for every AI model. Automatic cost savings, built-in data
            protection, and analytics that make sense. No rewrite required.
          </p>

          <div
            className="flex justify-center anim d4"
            style={{ gap: 10, marginBottom: 20 }}
          >
            <Link
              href="/signup"
              className="btn btn-lg brand-cta"
              style={{
                border: "1px solid #1a1f36",
                boxShadow: "0 10px 24px -8px rgba(26,31,54,0.35)",
                fontWeight: 500,
                color: "#ffffff",
              }}
            >
              Start free <span className="arrow" aria-hidden>→</span>
            </Link>
            <a
              href="#how"
              className="btn btn-lg"
              style={{
                background: "rgba(255,255,255,0.75)",
                color: "#0f172a",
                border: "1px solid rgba(148,163,184,0.45)",
                backdropFilter: "blur(8px)",
                WebkitBackdropFilter: "blur(8px)",
              }}
            >
              See how it works
            </a>
          </div>

          <div
            className="flex justify-center flex-wrap anim d5"
            style={{
              gap: 20,
              fontSize: 13,
              color: "#64748b",
            }}
          >
            <span>
              <span style={{ color: "#10b981", fontWeight: 700, marginRight: 6 }}>✓</span>
              Free 200K tokens
            </span>
            <span>
              <span style={{ color: "#10b981", fontWeight: 700, marginRight: 6 }}>✓</span>
              No credit card
            </span>
            <span>
              <span style={{ color: "#10b981", fontWeight: 700, marginRight: 6 }}>✓</span>
              5-minute setup
            </span>
          </div>
        </div>

        <div className="anim d6" style={{ marginTop: 80, position: "relative" }}>
          <FlowDiagram />
        </div>
      </div>
    </section>
  );
}
