import Link from "next/link";
import type { ReactNode } from "react";

type Variant = "primary" | "secondary";

type ActionProp = {
  label: string;
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

function classes(variant: Variant = "primary") {
  return variant === "secondary"
    ? "bg-white border border-gray-200 text-gray-700 hover:bg-gray-50"
    : "bg-gray-900 text-white hover:bg-black";
}

function ActionButton({ action }: { action: ActionProp }) {
  const cls = `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${classes(action.variant)}`;
  if (action.href) {
    return (
      <Link href={action.href} className={cls}>
        {action.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={action.onClick} className={cls}>
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
      <div className="w-16 h-16 rounded-2xl bg-violet-50 text-violet-500 flex items-center justify-center mb-5">
        {icon}
      </div>
      <h3 className="text-lg font-heading font-semibold text-gray-900 mb-2">
        {title}
      </h3>
      <p className="text-sm text-gray-500 max-w-md mb-6 leading-relaxed">
        {description}
      </p>
      {action ? (
        <div className="flex items-center gap-3">
          <ActionButton action={action} />
          {secondaryAction ? (
            <Link
              href={secondaryAction.href}
              className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 hover:text-gray-900"
            >
              {secondaryAction.label}
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
