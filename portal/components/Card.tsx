import { HTMLAttributes } from "react";

interface Props extends HTMLAttributes<HTMLDivElement> {
  padding?: "none" | "sm" | "md" | "lg";
}

const PADDING = {
  none: "",
  sm: "p-4",
  md: "p-6",
  lg: "p-8",
};

export function Card({ padding = "md", className = "", style, children, ...rest }: Props) {
  return (
    <div
      className={["rounded-xl", PADDING[padding], className].join(" ")}
      style={{
        background:
          "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}
