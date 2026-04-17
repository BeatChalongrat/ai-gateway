export default function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="sran-page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <div>
        <h1 className="sran-page-title">{title}</h1>
        {subtitle && <p className="sran-page-subtitle">{subtitle}</p>}
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  );
}
