import Link from "next/link";
import type { ReactNode } from "react";

type Variant = "primary" | "secondary";

type ActionProp = {
  label: ReactNode;
  href?: string;
  onClick?: () => void;
  variant?: Variant;
};

type Props = {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ActionProp;
  secondaryAction?: { label: string; href: string };
};

function buttonStyle(variant: Variant = "primary"): React.CSSProperties {
  if (variant === "secondary") {
    return {
      background: "var(--bg-surface)",
      border: "1px solid var(--border-default)",
      color: "var(--text-primary)",
    };
  }
  return {
    background: "var(--accent-primary)",
    color: "#FFFFFF",
    border: "1px solid var(--accent-primary)",
  };
}

function ActionButton({ action }: { action: ActionProp }) {
  const style: React.CSSProperties = {
    ...buttonStyle(action.variant),
    padding: "8px 16px",
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 500,
    textDecoration: "none",
    display: "inline-flex",
    alignItems: "center",
    cursor: "pointer",
  };
  if (action.href) {
    return (
      <Link href={action.href} style={style}>
        {action.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={action.onClick} style={style}>
      {action.label}
    </button>
  );
}

export default function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
}: Props) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-6 py-16">
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center mb-5"
        style={{
          background: "rgba(99,102,241,0.10)",
          border: "1px solid rgba(99,102,241,0.20)",
          color: "var(--accent-primary)",
        }}
      >
        {icon}
      </div>
      <h3
        className="font-heading"
        style={{
          fontSize: 17,
          fontWeight: 600,
          color: "var(--text-primary)",
          marginBottom: 8,
        }}
      >
        {title}
      </h3>
      <p
        style={{
          fontSize: 13,
          color: "var(--text-secondary)",
          maxWidth: 420,
          marginBottom: 24,
          lineHeight: 1.55,
        }}
      >
        {description}
      </p>
      {action ? (
        <div className="flex items-center gap-3">
          <ActionButton action={action} />
          {secondaryAction ? (
            <Link
              href={secondaryAction.href}
              style={{
                padding: "8px 16px",
                borderRadius: 8,
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-secondary)",
                textDecoration: "none",
              }}
            >
              {secondaryAction.label}
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
