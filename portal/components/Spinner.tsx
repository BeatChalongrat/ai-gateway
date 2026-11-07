interface Props {
  size?: number;
  className?: string;
}

export function Spinner({ size = 20, className = "" }: Props) {
  return (
    <span
      style={{
        width: size,
        height: size,
        borderColor: "var(--border-default)",
        borderTopColor: "var(--accent-primary)",
      }}
      className={["inline-block border-2 rounded-full animate-spin", className].join(" ")}
      role="status"
      aria-label="loading"
    />
  );
}

export function PageSpinner() {
  return (
    <div className="w-full flex items-center justify-center py-16">
      <Spinner size={28} />
    </div>
  );
}
