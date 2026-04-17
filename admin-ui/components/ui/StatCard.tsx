export default function StatCard({
  label,
  value,
  subtitle,
  trend,
}: {
  label: string;
  value: string | number;
  subtitle?: string;
  trend?: "active" | "up" | "down" | "neutral";
}) {
  return (
    <div className="sran-card" style={{ padding: "1rem 1.25rem", position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", top: 0, left: 0, width: "3px", height: "100%", background: "var(--color-sran-600)", borderRadius: "0 2px 2px 0" }} />
      <div className="sran-stat">
        <span className="sran-stat-label">{label}</span>
        <div style={{ display: "flex", alignItems: "baseline", gap: "0.375rem", marginTop: "0.125rem" }}>
          <span className="sran-stat-value">{value}</span>
          {trend === "active" && <span className="sran-trend-up">● live</span>}
          {trend === "up" && <span className="sran-trend-up">↑</span>}
          {trend === "down" && <span style={{ color: "var(--color-status-error)", fontSize: "var(--font-size-xs)", fontWeight: 600 }}>↓</span>}
          {trend === "neutral" && <span className="sran-trend-neutral">—</span>}
        </div>
        {subtitle && (
          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.125rem" }}>
            {subtitle}
          </span>
        )}
      </div>
    </div>
  );
}
