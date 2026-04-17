const WIDTHS = ['60%', '75%', '85%', '65%', '80%', '70%', '55%', '90%'];

export function SkeletonRow({ cols = 5 }: { cols?: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i}>
          <div className="sran-skeleton" style={{ width: WIDTHS[i % WIDTHS.length] }} />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonTable({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="sran-table">
        <thead>
          <tr>
            {Array.from({ length: cols }).map((_, i) => (
              <th key={i}>
                <div className="sran-skeleton" style={{ width: "60%" }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, i) => (
            <SkeletonRow key={i} cols={cols} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SkeletonCards({ count = 4 }: { count?: number }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "1rem" }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="sran-card">
          <div className="sran-skeleton" style={{ width: "60%", height: "1.25rem", marginBottom: "0.75rem" }} />
          <div className="sran-skeleton" style={{ width: "40%", height: "0.875rem", marginBottom: "0.5rem" }} />
          <div className="sran-skeleton" style={{ width: "80%", height: "0.875rem" }} />
        </div>
      ))}
    </div>
  );
}
