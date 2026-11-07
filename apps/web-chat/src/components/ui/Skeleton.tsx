"use client";

// Generic shimmer skeleton used by loading states. The `.skeleton`
// class lives in globals.css so other surfaces can reuse it directly.

import { useT } from "@/lib/i18n";

export function Skeleton({
  className = "",
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return <div className={`skeleton ${className}`} style={style} aria-hidden />;
}

export function ConversationListSkeleton({ rows = 4 }: { rows?: number }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-1.5 px-2 py-2" aria-label={t("a11y.loadingConversations")}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function MessageThreadSkeleton() {
  const t = useT();
  return (
    <div className="flex flex-col gap-6" aria-label={t("a11y.loadingConversation")}>
      <div className="flex justify-end">
        <Skeleton className="h-10 w-2/3 rounded-2xl" />
      </div>
      <div className="flex gap-3">
        <Skeleton className="h-6 w-6 rounded-full" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      </div>
    </div>
  );
}
