const VARIANTS: Record<string, string> = {
  success: "sran-badge-success",
  warning: "sran-badge-warning",
  error: "sran-badge-error",
  info: "sran-badge-info",
  neutral: "sran-badge-neutral",
};

export default function Badge({
  children,
  variant = "neutral",
  title,
}: {
  children: React.ReactNode;
  variant?: keyof typeof VARIANTS;
  title?: string;
}) {
  return (
    <span
      className={`sran-badge ${VARIANTS[variant] || VARIANTS.neutral}`}
      title={title}
      style={title ? { cursor: "help" } : undefined}
    >
      {children}
    </span>
  );
}
