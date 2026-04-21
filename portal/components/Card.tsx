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

export function Card({ padding = "md", className = "", children, ...rest }: Props) {
  return (
    <div
      className={[
        "bg-bg-card rounded-xl border border-gray-200 shadow-sm",
        PADDING[padding],
        className,
      ].join(" ")}
      {...rest}
    >
      {children}
    </div>
  );
}
