import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function CTASection() {
  return (
    <section className="cta-section" id="cta">
      <div className="container">
        <div
          className="cta-box reveal relative overflow-hidden"
          style={{
            background:
              "linear-gradient(135deg, #7F77DD 0%, #6B61D0 50%, #534AB7 100%)",
            borderRadius: 24,
            padding: "72px 48px",
            textAlign: "center",
            // Three-layer elevation cast down-right so the card reads as
            // lit from the upper-left. Positive x-offsets (40/20/10) plus
            // deeper y-offsets push the shadow off to the corner instead
            // of blooming symmetrically around the card.
            boxShadow: [
              "40px 60px 80px -20px rgba(83, 74, 183, 0.45)",
              "20px 30px 50px -15px rgba(83, 74, 183, 0.3)",
              "10px 15px 25px -8px rgba(60, 52, 137, 0.2)",
            ].join(", "),
          }}
        >
          <h2 style={{ color: "#ffffff" }}>Ready to simplify your AI stack?</h2>
          <p style={{ color: "rgba(255,255,255,0.85)" }}>
            Start free with 200,000 tokens. Top up anytime, no subscription.
          </p>
          <Link
            href="/signup"
            className="btn btn-lg"
            style={{
              background: "#ffffff",
              color: "#4f46e5",
              fontWeight: 500,
              border: "1px solid rgba(255,255,255,0.6)",
              boxShadow: "0 4px 12px rgba(0, 0, 0, 0.08)",
            }}
          >
            Create your account{" "}
            <span className="arrow" aria-hidden="true">
              <ArrowRight size={16} />
            </span>
          </Link>
        </div>
      </div>
    </section>
  );
}
