"use client";

import { usePathname } from "next/navigation";

// Client wrapper that forces the enter animation to retrigger on every
// route change. App Router does NOT remount layouts when navigating
// between sibling pages, so a CSS animation declared on <main> fires
// once on initial mount and never again. By using pathname as the key,
// React unmounts+remounts the inner div each time the URL changes,
// which restarts the keyframe.
export function PageEnter({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="page-enter">
      {children}
    </div>
  );
}
