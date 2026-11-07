"use client";

import { useMemo } from "react";

type Range = "today" | "7d" | "30d" | "all";

type UsagePoint = { created_at?: string | null };

type Props = {
  points: UsagePoint[];
  totalRequests: number;
  range: Range;
};

const RANGE_LABEL: Record<Range, string> = {
  today: "Today",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
};

// Faint backdrop bars for the empty state only. Deterministic so SSR/CSR
// render identical markup before any data arrives.
const PLACEHOLDER_HEIGHTS: number[] = Array.from({ length: 24 }, (_, i) => {
  return 20 + ((i * 37) % 60);
});

// Bucket the in-range request timestamps into evenly-spaced time slices.
// Bucket count is chosen per range (24 hourly for today, daily for 7d/30d,
// and an even 24-way split across the data span for "all").
function bucketCounts(points: UsagePoint[], range: Range): number[] {
  const now = Date.now();
  let count: number;
  let startMs: number;
  if (range === "today") {
    count = 24;
    startMs = now - 24 * 60 * 60 * 1000;
  } else if (range === "7d") {
    count = 7;
    startMs = now - 7 * 24 * 60 * 60 * 1000;
  } else if (range === "30d") {
    count = 30;
    startMs = now - 30 * 24 * 60 * 60 * 1000;
  } else {
    // all: span from the earliest point to now, split into 24 slices.
    count = 24;
    let earliest = now;
    for (const p of points) {
      if (!p.created_at) continue;
      const t = new Date(p.created_at).getTime();
      if (!Number.isNaN(t) && t < earliest) earliest = t;
    }
    startMs = earliest < now ? earliest : now - 60 * 60 * 1000;
  }
  const span = Math.max(1, now - startMs);
  const bucketMs = span / count;
  const buckets = new Array<number>(count).fill(0);
  for (const p of points) {
    if (!p.created_at) continue;
    const t = new Date(p.created_at).getTime();
    if (Number.isNaN(t) || t < startMs) continue;
    let idx = Math.floor((t - startMs) / bucketMs);
    if (idx < 0) idx = 0;
    if (idx >= count) idx = count - 1;
    buckets[idx] += 1;
  }
  return buckets;
}

export default function UsageChart({ points, totalRequests, range }: Props) {
  const hasData = totalRequests > 0;
  const counts = useMemo(() => bucketCounts(points, range), [points, range]);
  const maxCount = Math.max(1, ...counts);

  return (
    <div
      className="p-6"
      style={{
        background:
          "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        borderRadius: 12,
      }}
    >
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3
            className="font-heading"
            style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}
          >
            Requests over time
          </h3>
          <p style={{ fontSize: 12.5, color: "var(--text-tertiary)", marginTop: 2 }}>
            {RANGE_LABEL[range]}
          </p>
        </div>
        {hasData ? (
          <div className="flex items-center gap-4" style={{ fontSize: 11 }}>
            <div className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-sm"
                style={{ background: "var(--accent-primary)" }}
              />
              <span style={{ color: "var(--text-secondary)" }}>Requests</span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="relative h-48">
        {!hasData ? (
          <>
            <div className="absolute inset-0 flex items-end gap-1.5">
              {PLACEHOLDER_HEIGHTS.map((h, i) => (
                <div
                  key={i}
                  className="flex-1 rounded-t"
                  style={{ height: `${h}%`, background: "var(--bg-elevated)" }}
                />
              ))}
            </div>
            <div className="absolute inset-0 flex items-center justify-center">
              <div
                className="empty-float px-5 py-3 rounded-lg text-center"
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-default)",
                  boxShadow: "0 1px 4px rgba(0,0,0,0.05)",
                }}
              >
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: "var(--text-primary)",
                  }}
                >
                  Awaiting data
                </div>
                <div
                  style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 2 }}
                >
                  Send your first request to see it here
                </div>
              </div>
            </div>
          </>
        ) : (
          // Real per-bucket request counts (normalised to the busiest bucket).
          <div className="absolute inset-0 flex items-end gap-1.5">
            {counts.map((c, i) => {
              const h = c > 0 ? Math.max(4, Math.round((c / maxCount) * 100)) : 2;
              return (
                <div
                  key={i}
                  className="flex-1 rounded-t transition-opacity bar-grow"
                  title={`${c} request${c !== 1 ? "s" : ""}`}
                  style={{
                    height: `${h}%`,
                    background: "var(--accent-primary)",
                    opacity: 0.6,
                    animationDelay: `${i * 30}ms`,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.opacity = "1";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.opacity = "0.6";
                  }}
                />
              );
            })}
          </div>
        )}
      </div>

      <div
        className="flex items-center justify-between mt-4 pt-3"
        style={{
          fontSize: 11,
          color: "var(--text-tertiary)",
          borderTop: "1px solid var(--border-subtle)",
        }}
      >
        <span>Earliest</span>
        <span>Now</span>
      </div>
    </div>
  );
}
