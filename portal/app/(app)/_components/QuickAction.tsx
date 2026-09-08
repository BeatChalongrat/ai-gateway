import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";

type Props = {
  icon: ReactNode;
  title: string;
  description: string;
  href: string;
};

export default function QuickAction({ icon, title, description, href }: Props) {
  return (
    <Link
      href={href}
      className="group flex items-start gap-4 p-5 rounded-xl h-full transition-all"
      style={{
        background:
          "linear-gradient(180deg, var(--bg-surface), var(--bg-surface-2))",
        border: "1px solid var(--border-default)",
        textDecoration: "none",
      }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{
          background: "rgba(99, 102, 241, 0.12)",
          color: "var(--accent-text)",
        }}
      >
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div
          className="font-heading mb-0.5 flex items-center gap-1.5"
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {title}
          <span
            className="inline-flex transition-transform group-hover:translate-x-0.5"
            style={{ color: "var(--accent-text)" }}
          >
            <ArrowRight size={14} />
          </span>
        </div>
        <div
          style={{
            fontSize: 12.5,
            color: "var(--text-secondary)",
            lineHeight: 1.5,
          }}
        >
          {description}
        </div>
      </div>
    </Link>
  );
}
