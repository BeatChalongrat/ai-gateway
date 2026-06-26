export default function EmptyState({
  message = "No data available",
  icon = "○",
  action,
}: {
  message?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="sran-empty">
      <div className="sran-empty-icon">{icon}</div>
      <p style={{ marginBottom: action ? "1rem" : 0 }}>{message}</p>
      {action}
    </div>
  );
}
