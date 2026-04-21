import Link from "next/link";
import type { ReactNode } from "react";

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
      className="group flex items-start gap-4 p-5 rounded-xl border border-gray-200 bg-white hover:border-violet-400 hover:shadow-md hover:-translate-y-0.5 transition-all h-full"
    >
      <div className="w-10 h-10 rounded-lg bg-violet-50 text-violet-600 flex items-center justify-center flex-shrink-0 group-hover:bg-violet-100 transition-colors">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-heading font-semibold text-gray-900 mb-0.5 flex items-center gap-1.5">
          {title}
          <span className="text-gray-300 group-hover:text-violet-500 group-hover:translate-x-0.5 transition-all">
            →
          </span>
        </div>
        <div className="text-sm text-gray-500 leading-relaxed">{description}</div>
      </div>
    </Link>
  );
}
