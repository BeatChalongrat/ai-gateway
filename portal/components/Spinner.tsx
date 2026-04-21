interface Props {
  size?: number;
  className?: string;
}

export function Spinner({ size = 20, className = "" }: Props) {
  return (
    <span
      style={{ width: size, height: size }}
      className={["inline-block border-2 border-gray-300 border-t-brand-navy rounded-full animate-spin", className].join(" ")}
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
