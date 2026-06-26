/**
 * StatCardRowSkeleton — a responsive row of stat-card-shaped shimmer blocks.
 * Mirrors the real stats grid (grid-cols-1 md:grid-cols-3 gap-3).
 * Presentational only; uses the shared `.skeleton` shimmer from globals.css.
 */
export function StatCardRowSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3" aria-hidden>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 96, borderRadius: 10 }} />
      ))}
    </div>
  );
}

export default StatCardRowSkeleton;
