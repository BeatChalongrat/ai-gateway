"use client";

import { resolveAvatarUrl } from "@/lib/avatar";

interface Props {
  url?: string | null;
  name?: string | null;
  email?: string | null;
  id?: string | null;
  size?: number;
  radius?: number | "full";
  ring?: boolean;
  className?: string;
}

// 8 deterministic hues so the same user always gets the same colour.
// Picked to sit on the same saturation/lightness as the sidebar avatar
// gradient so we read as "family-same" across surfaces.
const PALETTE = [
  ["#6366F1", "#8B5CF6"], // indigo / violet
  ["#06B6D4", "#6366F1"], // cyan / indigo
  ["#F59E0B", "#F97316"], // amber / orange
  ["#10B981", "#06B6D4"], // emerald / cyan
  ["#EC4899", "#8B5CF6"], // pink / violet
  ["#0EA5E9", "#6366F1"], // sky / indigo
  ["#F43F5E", "#F59E0B"], // rose / amber
  ["#8B5CF6", "#EC4899"], // violet / pink
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0;
  }
  return h;
}

function initial(name?: string | null, email?: string | null): string {
  const source = (name || email || "?").trim();
  if (!source) return "?";
  return source.slice(0, 1).toUpperCase();
}

export function Avatar({
  url,
  name,
  email,
  id,
  size = 32,
  radius = "full",
  ring = false,
  className,
}: Props) {
  const seed = id || email || name || "?";
  const [from, to] = PALETTE[hashString(seed) % PALETTE.length];
  const borderRadius = radius === "full" ? "50%" : radius;
  const resolved = resolveAvatarUrl(url);
  const glyph = initial(name, email);

  return (
    <div
      className={[className, "avatar-hover"].filter(Boolean).join(" ")}
      style={{
        width: size,
        height: size,
        borderRadius,
        overflow: "hidden",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: resolved ? "transparent" : `linear-gradient(135deg, ${from}, ${to})`,
        color: "#FFFFFF",
        fontWeight: 600,
        fontSize: Math.max(10, Math.round(size * 0.42)),
        flexShrink: 0,
        boxShadow: ring
          ? "0 0 0 2px var(--bg-surface), 0 0 0 3px rgba(99,102,241,0.4)"
          : undefined,
      }}
    >
      {resolved ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={resolved}
          alt={name || email || "Profile"}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : (
        <span>{glyph}</span>
      )}
    </div>
  );
}
