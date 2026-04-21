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
        <h1 className="text-2xl md:text-3xl font-heading font-bold text-gray-900 leading-tight">
          {title}
        </h1>
        {subtitle ? (
          <p className="text-sm text-gray-500 mt-1.5">{subtitle}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex items-center gap-3 shrink-0">{actions}</div>
      ) : null}
    </div>
  );
}
