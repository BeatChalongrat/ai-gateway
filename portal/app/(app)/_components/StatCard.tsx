import Link from "next/link";
import type { ReactNode } from "react";

type Tone = "violet" | "cyan" | "green" | "amber" | "rose" | "neutral";

type Trend = {
  direction: "up" | "down" | "flat";
  label: string;
};

type Props = {
  label: string;
  value: ReactNode;
  caption?: string;
  icon: ReactNode;
  tone?: Tone;
  trend?: Trend;
  href?: string;
};

// rgb triplets matching the new accent palette so we can build
// rgba() backgrounds and gradient accent lines without parsing hex.
const TONE_RGB: Record<Tone, string> = {
  violet: "99,102,241",
  cyan: "6,182,212",
  green: "16,185,129",
  amber: "245,158,11",
  rose: "248,113,113",
  neutral: "139,146,165",
};

export default function StatCard({
  label,
  value,
  caption,
  icon,
  tone = "violet",
  trend,
  href,
}: Props) {
  const rgb = TONE_RGB[tone];

  const body = (
    <div
      className="h-full"
      style={{
        position: "relative",
        background:
          "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 12,
        padding: 20,
        overflow: "hidden",
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 1.5,
          background: `linear-gradient(90deg, transparent, rgba(${rgb}, 0.65), transparent)`,
        }}
      />

      <div className="flex items-start justify-between mb-4">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center"
          style={{
            background: `rgba(${rgb}, 0.14)`,
            color: `rgb(${rgb})`,
          }}
        >
          {icon}
        </div>
        {trend ? (
          <span
            className="text-xs font-medium px-2 py-1 rounded-md"
            style={{
              background:
                trend.direction === "up"
                  ? "rgba(16,185,129,0.12)"
                  : trend.direction === "down"
                  ? "rgba(248,113,113,0.12)"
                  : "var(--bg-elevated)",
              color:
                trend.direction === "up"
                  ? "var(--accent-success)"
                  : trend.direction === "down"
                  ? "var(--accent-danger)"
                  : "var(--text-secondary)",
            }}
          >
            {trend.direction === "up"
              ? "↑"
              : trend.direction === "down"
              ? "↓"
              : "—"}{" "}
            {trend.label}
          </span>
        ) : null}
      </div>
      <div
        style={{
          fontSize: 10.5,
          textTransform: "uppercase",
          letterSpacing: "0.14em",
          color: "var(--text-tertiary)",
          fontWeight: 600,
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        className="font-heading"
        style={{
          fontSize: 28,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "var(--text-primary)",
          lineHeight: 1.1,
        }}
      >
        {value}
      </div>
      {caption ? (
        <div style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 6 }}>
          {caption}
        </div>
      ) : null}
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block h-full" style={{ textDecoration: "none" }}>
        {body}
      </Link>
    );
  }
  return body;
}
