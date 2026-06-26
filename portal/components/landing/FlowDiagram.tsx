"use client";

/**
 * Landing flow diagram: Your App → SRAN Gateway → 14 Providers.
 * Light container that matches hero; Gateway card remains the only
 * purple-gradient element so it pops as the page's hero visual.
 */
import { Check } from "lucide-react";

function AppTileGrid() {
  return (
    <div
      aria-hidden
      style={{
        width: 38,
        height: 38,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gridTemplateRows: "1fr 1fr",
        gap: 4,
        padding: 4,
      }}
    >
      <div
        style={{
          background: "linear-gradient(135deg, #818cf8, #6366f1)",
          borderRadius: 6,
          boxShadow: "0 1px 3px rgba(99,102,241,0.3)",
        }}
      />
      <div
        style={{
          background: "linear-gradient(135deg, #c084fc, #a855f7)",
          borderRadius: 6,
          boxShadow: "0 1px 3px rgba(168,85,247,0.3)",
        }}
      />
      <div
        style={{
          background: "linear-gradient(135deg, #f0abfc, #d946ef)",
          borderRadius: 6,
          boxShadow: "0 1px 3px rgba(217,70,239,0.3)",
        }}
      />
      <div
        style={{
          background: "linear-gradient(135deg, #a78bfa, #8b5cf6)",
          borderRadius: 6,
          boxShadow: "0 1px 3px rgba(139,92,246,0.3)",
        }}
      />
    </div>
  );
}

function HexShieldIcon({ size = 52 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 52 52"
      width={size}
      height={size}
      fill="none"
      aria-hidden
    >
      <defs>
        <linearGradient id="hex-outer" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="rgba(255,255,255,0.4)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0.1)" />
        </linearGradient>
        <linearGradient id="hex-check" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="100%" stopColor="#e0e7ff" />
        </linearGradient>
      </defs>
      <path
        d="M26 4 L44 14 L44 38 L26 48 L8 38 L8 14 Z"
        fill="url(#hex-outer)"
        stroke="rgba(255,255,255,0.55)"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M26 14 L36 20 L36 32 L26 38 L16 32 L16 20 Z"
        fill="rgba(255,255,255,0.12)"
        stroke="rgba(255,255,255,0.7)"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M21 26 L25 30 L32 22"
        stroke="url(#hex-check)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}

function OrbitIcon() {
  return (
    <svg viewBox="0 0 36 36" width="32" height="32" fill="none" aria-hidden>
      <circle
        cx="18"
        cy="18"
        r="13"
        stroke="rgba(168,85,247,0.3)"
        strokeWidth="1"
        strokeDasharray="2 3"
        fill="none"
      />
      <circle
        cx="18"
        cy="18"
        r="9"
        stroke="rgba(168,85,247,0.4)"
        strokeWidth="1"
        fill="none"
      />
      <circle cx="18" cy="18" r="4" fill="#a855f7" />
      <g>
        <circle cx="18" cy="5" r="1.8" fill="#6366f1">
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 18 18"
            to="360 18 18"
            dur="8s"
            repeatCount="indefinite"
          />
        </circle>
      </g>
      <g>
        <circle cx="18" cy="5" r="1.6" fill="#8b5cf6">
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="90 18 18"
            to="450 18 18"
            dur="9.5s"
            repeatCount="indefinite"
          />
        </circle>
      </g>
      <g>
        <circle cx="18" cy="5" r="1.4" fill="#ec4899">
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="180 18 18"
            to="540 18 18"
            dur="11s"
            repeatCount="indefinite"
          />
        </circle>
      </g>
      <g>
        <circle cx="18" cy="5" r="1.4" fill="#f0abfc">
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="270 18 18"
            to="630 18 18"
            dur="10s"
            repeatCount="indefinite"
          />
        </circle>
      </g>
    </svg>
  );
}

function SideCard({
  label,
  title,
  subtitle,
  icon,
  iconFrameBg,
  iconFrameBorder,
}: {
  label: string;
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  iconFrameBg: string;
  iconFrameBorder: string;
}) {
  return (
    <div
      className="relative rounded-2xl overflow-hidden"
      style={{
        padding: "28px 22px",
        background: "#ffffff",
        border: "1px solid rgba(15,23,42,0.08)",
        boxShadow: "0 1px 3px rgba(15,23,42,0.04)",
      }}
    >
      <div
        className="text-center"
        style={{
          fontSize: 10,
          letterSpacing: "1.2px",
          color: "#94a3b8",
          textTransform: "uppercase",
          fontWeight: 600,
        }}
      >
        {label}
      </div>
      <div className="flex justify-center mt-4">
        <div
          className="rounded-xl grid place-items-center"
          style={{
            width: 56,
            height: 56,
            background: iconFrameBg,
            border: iconFrameBorder,
          }}
        >
          {icon}
        </div>
      </div>
      <div
        className="text-center mt-3"
        style={{ fontSize: 14, color: "#0f172a", fontWeight: 500 }}
      >
        {title}
      </div>
      <div
        className="text-center mt-1"
        style={{ fontSize: 11, color: "#64748b", lineHeight: 1.5 }}
      >
        {subtitle}
      </div>
    </div>
  );
}

function Pipe({ side }: { side: "left" | "right" }) {
  const packets =
    side === "left"
      ? [
          { r: 5, color: "#6366f1", delay: 0 },
          { r: 4, color: "#818cf8", delay: 0.7 },
          { r: 3, color: "#a78bfa", delay: 1.3 },
        ]
      : [
          { r: 5, color: "#8b5cf6", delay: 0 },
          { r: 4, color: "#a78bfa", delay: 0.9 },
          { r: 3, color: "#c4b5fd", delay: 1.6 },
        ];
  const dashColor =
    side === "left" ? "rgba(99,102,241,0.3)" : "rgba(139,92,246,0.3)";
  return (
    <svg
      className="absolute top-1/2 -translate-y-1/2"
      style={{
        [side === "left" ? "left" : "right"]: 0,
        width: "calc(50% - 100px)",
        height: 40,
      }}
      viewBox="0 0 200 40"
      preserveAspectRatio="none"
      aria-hidden
    >
      <path
        d="M 0 20 L 200 20"
        stroke={dashColor}
        strokeWidth="1.5"
        strokeDasharray="4 4"
        fill="none"
      />
      {packets.map((p, i) => (
        <circle key={i} r={p.r} fill={p.color} opacity={0}>
          <animateMotion
            path="M 0 20 L 200 20"
            dur="2s"
            begin={`${p.delay}s`}
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0;1;1;0"
            dur="2s"
            begin={`${p.delay}s`}
            repeatCount="indefinite"
          />
        </circle>
      ))}
    </svg>
  );
}

function GatewayCard() {
  return (
    <div
      className="relative rounded-2xl overflow-hidden"
      style={{
        width: 200,
        padding: "22px 18px",
        background:
          "linear-gradient(135deg, #4f46e5 0%, #7c3aed 50%, #a855f7 100%)",
        boxShadow:
          "0 10px 40px -8px rgba(124,58,237,0.4), inset 0 0 0 1px rgba(255,255,255,0.18)",
        zIndex: 2,
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0 rounded-2xl animate-pulse-ring pointer-events-none"
        style={{ border: "2px solid rgba(129,140,248,0.6)" }}
      />
      <div
        aria-hidden
        className="absolute inset-0 rounded-2xl animate-pulse-ring pointer-events-none"
        style={{
          border: "2px solid rgba(192,132,252,0.6)",
          animationDelay: "1.25s",
        }}
      />
      <div
        aria-hidden
        className="absolute inset-0 rounded-2xl pointer-events-none animate-sheen"
        style={{
          background:
            "linear-gradient(120deg, transparent 40%, rgba(255,255,255,0.15) 50%, transparent 60%)",
          backgroundSize: "200% 100%",
        }}
      />

      <div className="relative" style={{ zIndex: 1 }}>
        <div
          className="text-center"
          style={{
            fontSize: 10,
            letterSpacing: "1.5px",
            color: "rgba(255,255,255,0.85)",
            textTransform: "uppercase",
            fontWeight: 600,
          }}
        >
          SRAN Gateway
        </div>
        <div className="flex justify-center mt-2">
          <HexShieldIcon size={52} />
        </div>
        <div
          className="text-center mt-2"
          style={{ fontSize: 15, color: "#ffffff", fontWeight: 600 }}
        >
          Route · Protect · Track
        </div>
        <div
          className="text-center mt-1"
          style={{ fontSize: 10, color: "rgba(255,255,255,0.75)" }}
        >
          Every request secured
        </div>
        <div className="flex flex-wrap gap-1 justify-center mt-3">
          {["DLP", "Smart route", "Log & bill"].map((t) => (
            <span
              key={t}
              className="rounded-md backdrop-blur inline-flex items-center gap-1"
              style={{
                fontSize: 9,
                fontWeight: 500,
                padding: "3px 7px",
                color: "#ffffff",
                background: "rgba(255,255,255,0.18)",
                border: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              <Check size={11} /> {t}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function FlowDiagram() {
  return (
    <div
      className="relative mx-auto rounded-2xl overflow-hidden"
      style={{
        maxWidth: 960,
        padding: "44px 28px",
        background: "linear-gradient(180deg, #eceef7 0%, #e4e7f3 100%)",
        border: "1px solid rgba(15,23,42,0.08)",
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage:
            "linear-gradient(rgba(15,23,42,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.04) 1px, transparent 1px)",
          backgroundSize: "30px 30px",
          WebkitMaskImage:
            "radial-gradient(ellipse 80% 70% at 50% 50%, #000 30%, transparent 95%)",
          maskImage:
            "radial-gradient(ellipse 80% 70% at 50% 50%, #000 30%, transparent 95%)",
        }}
      />
      <div
        aria-hidden
        className="absolute rounded-full blur-3xl pointer-events-none animate-aurora-1"
        style={{
          background: "#a5b4fc",
          top: "-60px",
          left: "-60px",
          width: 280,
          height: 280,
          opacity: 0.5,
        }}
      />
      <div
        aria-hidden
        className="absolute rounded-full blur-3xl pointer-events-none animate-aurora-2"
        style={{
          background: "#c4b5fd",
          bottom: "-60px",
          right: "-60px",
          width: 280,
          height: 280,
          opacity: 0.45,
        }}
      />

      <div className="relative flow-grid" style={{ zIndex: 1 }}>
        <SideCard
          label="Your app"
          title="Send request"
          subtitle="Use any SDK you already have"
          icon={<AppTileGrid />}
          iconFrameBg="transparent"
          iconFrameBorder="none"
        />
        <div
          className="relative flex items-center justify-center"
          style={{ minHeight: 200 }}
        >
          <Pipe side="left" />
          <Pipe side="right" />
          <GatewayCard />
        </div>
        <SideCard
          label="14 providers"
          title="Best model wins"
          subtitle="OpenAI · Anthropic · Google · +11 more"
          icon={<OrbitIcon />}
          iconFrameBg="linear-gradient(135deg, #faf5ff, #fdf2f8)"
          iconFrameBorder="1px solid rgba(168,85,247,0.15)"
        />
      </div>

      <style jsx>{`
        .flow-grid {
          display: grid;
          grid-template-columns: 1fr 1.3fr 1fr;
          gap: 20px;
          align-items: center;
        }
        @media (max-width: 768px) {
          .flow-grid {
            grid-template-columns: 1fr;
            gap: 28px;
          }
        }
      `}</style>
    </div>
  );
}
