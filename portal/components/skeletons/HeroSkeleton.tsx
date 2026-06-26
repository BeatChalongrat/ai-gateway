/**
 * HeroSkeleton — placeholder for the credit/balance hero card.
 * A single large rounded shimmer block matching the real hero's footprint.
 * Presentational only; uses the shared `.skeleton` shimmer from globals.css.
 */
export function HeroSkeleton() {
  return (
    <div
      className="skeleton w-full"
      style={{ height: 160, borderRadius: 16 }}
      aria-hidden
    />
  );
}

export default HeroSkeleton;
