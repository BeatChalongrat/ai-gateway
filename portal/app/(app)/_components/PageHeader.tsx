import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
};

export default function PageHeader({ title, subtitle, actions }: Props) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 mb-2">
      <div className="min-w-0">
        <h1
          className="font-heading"
          style={{
            fontSize: 24,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            margin: "0 0 4px",
            color: "var(--text-primary)",
            lineHeight: 1.15,
          }}
        >
          {title}
        </h1>
        {subtitle ? (
          <p style={{ fontSize: 13, color: "var(--text-tertiary)", margin: 0 }}>
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex items-center gap-3 shrink-0">{actions}</div>
      ) : null}
    </div>
  );
}
