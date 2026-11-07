import { ThemeToggle } from "../ThemeToggle";

// `action` is kept for backwards compat with existing call sites; `actions`
// (plural) is the new name. Either renders on the right, with the theme
// toggle always appended so every page gets it automatically.
export default function PageHeader({
  title,
  subtitle,
  action,
  actions,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const right = actions ?? action;
  return (
    <div
      className="sran-page-header"
      style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}
    >
      <div style={{ minWidth: 0 }}>
        <h1 className="sran-page-title" style={{ overflowWrap: "anywhere" }}>{title}</h1>
        {subtitle && <p className="sran-page-subtitle">{subtitle}</p>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0, flexWrap: "wrap" }}>
        {right}
        <ThemeToggle />
      </div>
    </div>
  );
}
