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
}: {
  children: React.ReactNode;
  variant?: keyof typeof VARIANTS;
}) {
  return (
    <span className={`sran-badge ${VARIANTS[variant] || VARIANTS.neutral}`}>
      {children}
    </span>
  );
}
