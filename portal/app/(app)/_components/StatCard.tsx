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

const TONES: Record<Tone, { bg: string; text: string }> = {
  violet: { bg: "bg-violet-50", text: "text-violet-600" },
  cyan: { bg: "bg-cyan-50", text: "text-cyan-600" },
  green: { bg: "bg-green-50", text: "text-green-600" },
  amber: { bg: "bg-amber-50", text: "text-amber-600" },
  rose: { bg: "bg-rose-50", text: "text-rose-600" },
  neutral: { bg: "bg-gray-100", text: "text-gray-600" },
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
  const t = TONES[tone];
  const body = (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 transition-all hover:shadow-md hover:-translate-y-0.5 h-full">
      <div className="flex items-start justify-between mb-4">
        <div
          className={`${t.bg} ${t.text} w-10 h-10 rounded-lg flex items-center justify-center`}
        >
          {icon}
        </div>
        {trend ? (
          <span
            className={`text-xs font-medium px-2 py-1 rounded-md ${
              trend.direction === "up"
                ? "bg-green-50 text-green-700"
                : trend.direction === "down"
                  ? "bg-rose-50 text-rose-700"
                  : "bg-gray-100 text-gray-600"
            }`}
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
      <div className="text-xs uppercase tracking-widest text-gray-500 mb-1">
        {label}
      </div>
      <div className="text-3xl font-heading font-bold text-gray-900 leading-tight">
        {value}
      </div>
      {caption ? (
        <div className="text-sm text-gray-500 mt-1">{caption}</div>
      ) : null}
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block h-full">
        {body}
      </Link>
    );
  }
  return body;
}
