import Image from "next/image";

type Props = {
  size?: number;
  radius?: number;
  withShadow?: boolean;
  priority?: boolean;
};

export default function LogoTile({
  size = 44,
  radius = 10,
  withShadow = true,
  priority = false,
}: Props) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        overflow: "hidden",
        flexShrink: 0,
        boxShadow: withShadow
          ? "0 8px 24px rgba(0,0,0,0.15), 0 0 0 1px rgba(0,0,0,0.06)"
          : undefined,
      }}
    >
      <Image
        src="/sran-logo.png"
        alt="SRAN"
        width={size}
        height={size}
        priority={priority}
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
    </div>
  );
}
