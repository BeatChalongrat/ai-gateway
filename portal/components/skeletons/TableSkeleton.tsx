/**
 * TableSkeleton — a header bar plus N thin row bars approximating a table.
 * Kept intentionally simple: each row is a full-width shimmer bar.
 * `cols` is accepted for call-site parity but the simple layout uses
 * full-width bars rather than per-column cells.
 * Presentational only; uses the shared `.skeleton` shimmer from globals.css.
 */
export function TableSkeleton({
  rows = 6,
  cols,
}: {
  rows?: number;
  cols?: number;
}) {
  void cols;
  return (
    <div className="space-y-2" aria-hidden>
      {/* Header bar */}
      <div className="skeleton" style={{ height: 40, borderRadius: 8, opacity: 0.7 }} />
      {/* Rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 40, borderRadius: 8 }} />
      ))}
    </div>
  );
}

export default TableSkeleton;
