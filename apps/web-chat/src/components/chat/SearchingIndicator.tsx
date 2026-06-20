"use client";

// Loading indicator shown while the backend is searching the web (the
// `search_status: "searching"` SSE event). Sits in place of the typing
// dots; auto-hides once the answer streams in. Reuses the global
// .shimmer-text sweep so it matches the thinking/image indicators.

import { Search } from "lucide-react";

export function SearchingIndicator() {
  return (
    <div
      className="flex items-center gap-2.5 py-1"
      role="status"
      aria-label="กำลังค้นหาข้อมูล"
    >
      <Search className="h-4 w-4 animate-pulse text-text-tertiary" aria-hidden />
      <p className="shimmer-text text-sm font-medium">กำลังค้นหาข้อมูล...</p>
    </div>
  );
}
