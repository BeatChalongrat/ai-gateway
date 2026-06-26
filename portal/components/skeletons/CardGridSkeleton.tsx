/**
 * CardGridSkeleton — a responsive grid of card-shaped shimmer blocks.
 * Mirrors the real card grid (grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4).
 * Presentational only; uses the shared `.skeleton` shimmer from globals.css.
 */
export function CardGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div
      className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
      aria-hidden
    >
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 160, borderRadius: 16 }} />
      ))}
    </div>
  );
}

export default CardGridSkeleton;
