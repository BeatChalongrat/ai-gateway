"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  CreditCard,
  FolderOpen,
  Gem,
  LayoutDashboard,
  LogOut,
  Settings,
  TrendingUp,
  Users,
} from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
import { clearTokens } from "@/lib/auth";
import { formatTokens } from "@/lib/format";
import type { PortalUser } from "@/lib/types";
import { Avatar } from "./Avatar";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

interface NavItem {
  label: string;
  href: string;
  Icon: typeof LayoutDashboard;
}

const NAV: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", Icon: LayoutDashboard },
  { label: "Projects", href: "/projects", Icon: FolderOpen },
  { label: "Usage", href: "/usage", Icon: TrendingUp },
  { label: "Team", href: "/team", Icon: Users },
  { label: "Billing", href: "/billing", Icon: CreditCard },
  { label: "Settings", href: "/settings", Icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<PortalUser | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .get<PortalUser>("/portal/me")
      .then((u) => {
        if (alive) setUser(u);
      })
      .catch((e) => {
        if (!(e instanceof PortalApiError && e.status === 401)) {
          console.error("sidebar /portal/me:", e);
        }
      });
    return () => {
      alive = false;
    };
  }, [pathname]); // refresh on route change so post-topup reflects in sidebar

  function handleLogout() {
    api.post("/portal/auth/logout").catch(() => {});
    clearTokens();
    router.push("/login");
  }

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(href + "/");

  const sidebarBody = (
    <>
      <div className="px-5 py-6">
        <Logo variant="light" size="md" />
      </div>

      <nav className="flex-1 px-3">
        <ul className="space-y-0.5">
          {NAV.map((item) => {
            const active = isActive(item.href);
            return (
              <li key={item.href} className="relative">
                {/* Active-state accent bar — 3px violet rail flush to
                    the sidebar's left edge. */}
                {active ? (
                  <span
                    aria-hidden
                    className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-violet-500"
                  />
                ) : null}
                <Link
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={[
                    "nav-item flex items-center gap-3 pl-4 pr-3 py-2.5 rounded-lg text-sm font-medium",
                    active
                      ? "is-active bg-violet-500/10 text-white"
                      : "text-white/80 hover:bg-white/5 hover:text-white",
                  ].join(" ")}
                >
                  <item.Icon
                    size={18}
                    strokeWidth={1.75}
                    className={["shrink-0", active ? "text-violet-400" : "text-white/60"].join(" ")}
                  />
                  <span>{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer: credits / theme toggle / user card with logout icon.
          Each block stands alone — no dropdown. The logout icon
          replaces the old click-the-avatar-to-open-menu interaction;
          name + avatar are now non-interactive. */}
      <footer
        className="mt-auto px-3 pb-4 pt-3 flex flex-col gap-2.5 border-t border-white/10"
      >
        {/* Credits card — sidebar-scoped (translucent on navy, both themes) */}
        {user ? (
          <div
            style={{
              background:
                "linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.04))",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 10,
              padding: "10px 12px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <Gem size={13} strokeWidth={1.75} style={{ color: "#6366F1" }} />
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#FFFFFF",
                }}
              >
                {formatTokens(user.credit_balance)}
              </span>
              <span style={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}>credits</span>
            </div>
            <Link
              href="/billing"
              onClick={() => setMobileOpen(false)}
              style={{
                fontSize: 11,
                color: "#6366F1",
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
                fontWeight: 500,
              }}
            >
              Upgrade
              <ArrowUpRight size={11} />
            </Link>
          </div>
        ) : null}

        {/* Theme toggle */}
        <ThemeToggle />

        {/* User card with standalone logout icon — sidebar-scoped */}
        {user ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 10px",
              background:
                "linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.04))",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 10,
            }}
          >
            <Avatar
              url={user.avatar_url}
              name={user.display_name}
              email={user.email}
              id={user.id}
              size={28}
              radius={7}
            />
            <div style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 500,
                  color: "#FFFFFF",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {user.display_name || "—"}
              </div>
              <div
                style={{
                  fontSize: 10,
                  color: "rgba(255,255,255,0.6)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {user.email || ""}
              </div>
            </div>
            <button
              onClick={handleLogout}
              aria-label="Sign out"
              title="Sign out"
              type="button"
              style={{
                width: 28,
                height: 28,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "transparent",
                border: "none",
                borderRadius: 6,
                color: "rgba(255,255,255,0.55)",
                cursor: "pointer",
                flexShrink: 0,
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "rgba(248,113,113,0.15)";
                e.currentTarget.style.color = "#F87171";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "rgba(255,255,255,0.55)";
              }}
            >
              <LogOut size={14} strokeWidth={1.75} />
            </button>
          </div>
        ) : null}
      </footer>
    </>
  );

  return (
    <>
      {/* Mobile top bar */}
      <div
        className="md:hidden h-14 flex items-center justify-between px-4 text-white"
        style={{ backgroundColor: "var(--bg-sidebar)" }}
      >
        <Logo variant="light" size="sm" />
        <button
          aria-label="Open menu"
          onClick={() => setMobileOpen(true)}
          className="text-white text-2xl leading-none"
        >
          ≡
        </button>
      </div>

      {/* Desktop sidebar — navy in BOTH themes (brand signature). */}
      <aside
        className="hidden md:flex md:flex-col md:fixed md:inset-y-0 md:left-0 md:w-60 text-white"
        style={{ backgroundColor: "var(--bg-sidebar)" }}
      >
        {sidebarBody}
      </aside>

      {/* Mobile drawer */}
      {mobileOpen ? (
        <div className="md:hidden fixed inset-0 z-50 flex">
          <div
            className="flex-1 bg-black/50"
            onClick={() => setMobileOpen(false)}
          />
          <div
            className="w-64 flex flex-col text-white"
            style={{ backgroundColor: "var(--bg-sidebar)" }}
          >
            <div className="flex justify-end p-3">
              <button
                aria-label="Close menu"
                onClick={() => setMobileOpen(false)}
                className="text-white text-xl leading-none px-2"
              >
                ×
              </button>
            </div>
            {sidebarBody}
          </div>
        </div>
      ) : null}
    </>
  );
}
