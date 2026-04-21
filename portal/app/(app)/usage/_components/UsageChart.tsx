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
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="font-heading font-semibold text-gray-900">
            Requests over time
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">{RANGE_LABEL[range]}</p>
        </div>
        {hasData ? (
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-sm bg-violet-500" />
              <span className="text-gray-600">Requests</span>
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
                  className="flex-1 bg-gray-100 rounded-t"
                  style={{ height: `${h}%` }}
                />
              ))}
            </div>
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="bg-white/90 backdrop-blur-sm px-5 py-3 rounded-lg border border-gray-200 shadow-sm text-center">
                <div className="text-sm font-medium text-gray-900">
                  Awaiting data
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
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
                className="flex-1 bg-violet-500 rounded-t opacity-80 hover:opacity-100 transition-opacity"
                style={{ height: `${h}%` }}
              />
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between mt-4 text-xs text-gray-400 border-t border-gray-100 pt-3">
        <span>Earliest</span>
        <span>Now</span>
      </div>
    </div>
  );
}
