"use client";

import { usePathname } from "next/navigation";
import AppShell from "./AppShell";

// Routes that should render WITHOUT the sidebar shell. Login is full-screen
// and pre-auth, so it sits outside the AppShell. Anything else gets the
// shared sidebar + main layout, mounted once at the root so navigations
// don't remount Sidebar (preserves scroll position and any local state).
const NO_SHELL_PATHS = new Set<string>(["/login"]);

export default function ShellWrapper({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (NO_SHELL_PATHS.has(pathname)) return <>{children}</>;
  return <AppShell>{children}</AppShell>;
}
