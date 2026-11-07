import Image from "next/image";

interface Props {
  variant?: "dark" | "light";
  size?: "sm" | "md" | "lg";
}

const SIZE = {
  sm: { wrap: "gap-2", px: 32, box: "rounded-lg", title: "text-sm", sub: "text-[10px]" },
  md: { wrap: "gap-2.5", px: 40, box: "rounded-lg", title: "text-base", sub: "text-[11px]" },
  lg: { wrap: "gap-3", px: 56, box: "rounded-xl", title: "text-xl", sub: "text-xs" },
};

export function Logo({ variant = "dark", size = "md" }: Props) {
  const s = SIZE[size];
  const titleColor = variant === "dark" ? "text-gray-900" : "text-white";
  const subColor = variant === "dark" ? "text-gray-500" : "text-white/60";
  return (
    <div className={["inline-flex items-center", s.wrap].join(" ")}>
      <Image
        src="/sran-logo.png"
        alt="SRAN"
        width={s.px}
        height={s.px}
        className={["overflow-hidden flex-shrink-0", s.box].join(" ")}
        priority
      />
      <div className="leading-tight">
        <div className={["font-heading font-extrabold tracking-tight", s.title, titleColor].join(" ")}>SRAN</div>
        <div className={["uppercase tracking-[0.2em] font-semibold", s.sub, subColor].join(" ")}>AI Gateway</div>
      </div>
    </div>
  );
}
