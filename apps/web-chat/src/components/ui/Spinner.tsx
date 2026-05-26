export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      style={{ width: size, height: size }}
      className="inline-block animate-spin rounded-full border-2 border-text-secondary/30 border-t-accent"
      aria-hidden
    />
  );
}
