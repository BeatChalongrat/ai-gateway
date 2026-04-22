"use client";

type Range = "today" | "7d" | "30d" | "all";

type Props = {
  totalRequests: number;
  range: Range;
};

const RANGE_LABEL: Record<Range, string> = {
  today: "Today",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
};

// Deterministic heights so SSR and CSR render identical markup. 24
// bars at 20-80% heights creates a natural-looking sparkline shape.
const PLACEHOLDER_HEIGHTS: number[] = Array.from({ length: 24 }, (_, i) => {
  return 20 + ((i * 37) % 60);
});

export default function UsageChart({ totalRequests, range }: Props) {
  const hasData = totalRequests > 0;

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
                className="px-5 py-3 rounded-lg text-center"
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
          /* Placeholder until a chart library is wired up. */
          <div className="absolute inset-0 flex items-end gap-1.5">
            {PLACEHOLDER_HEIGHTS.map((h, i) => (
              <div
                key={i}
                className="flex-1 rounded-t transition-opacity"
                style={{
                  height: `${h}%`,
                  background: "var(--accent-primary)",
                  opacity: 0.6,
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.opacity = "1";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.opacity = "0.6";
                }}
              />
            ))}
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
