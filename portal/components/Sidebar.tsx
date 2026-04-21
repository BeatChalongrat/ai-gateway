"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  CreditCard,
  FolderOpen,
  Gem,
  LayoutDashboard,
  LogOut,
  Settings,
  TrendingUp,
} from "lucide-react";

import { api, PortalApiError } from "@/lib/api";
import { clearTokens } from "@/lib/auth";
import { resolveAvatarUrl } from "@/lib/avatar";
import { formatTokens } from "@/lib/format";
import type { PortalUser } from "@/lib/types";
import { Logo } from "./Logo";

interface NavItem {
  label: string;
  href: string;
  Icon: typeof LayoutDashboard;
}

const NAV: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", Icon: LayoutDashboard },
  { label: "Projects", href: "/projects", Icon: FolderOpen },
  { label: "Usage", href: "/usage", Icon: TrendingUp },
  { label: "Billing", href: "/billing", Icon: CreditCard },
  { label: "Settings", href: "/settings", Icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<PortalUser | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!menuOpen) return;
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

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
                    "flex items-center gap-3 pl-4 pr-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                    active
                      ? "bg-violet-500/10 text-white"
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

      {/* Mini credit indicator — only for portal users, not visible when
          the /me call hasn't landed yet. */}
      {user ? (
        <div className="px-4 pb-3">
          <div className="rounded-lg bg-white/5 border border-white/10 px-3 py-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-white/90">
                <Gem size={14} strokeWidth={1.75} className="text-brand-blue" />
                <span className="text-sm font-medium">
                  {formatTokens(user.credit_balance)}
                </span>
                <span className="text-xs text-white/50">credits</span>
              </div>
            </div>
            <Link
              href="/billing"
              onClick={() => setMobileOpen(false)}
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-brand-blue hover:underline"
            >
              Upgrade
              <ArrowUpRight size={12} />
            </Link>
          </div>
        </div>
      ) : null}

      {/* User block + dropdown */}
      <div
        ref={menuRef}
        className="px-3 pb-4 pt-2 border-t border-white/10 relative"
      >
        {menuOpen ? (
          <div className="absolute bottom-full left-3 right-3 mb-1 rounded-lg bg-brand-dark border border-white/10 shadow-xl overflow-hidden">
            <Link
              href="/settings"
              onClick={() => {
                setMenuOpen(false);
                setMobileOpen(false);
              }}
              className="flex items-center gap-2 px-3 py-2.5 text-sm text-white/80 hover:bg-white/5"
            >
              <Settings size={14} />
              Settings
            </Link>
            <button
              onClick={() => {
                setMenuOpen(false);
                handleLogout();
              }}
              className="w-full flex items-center gap-2 px-3 py-2.5 text-sm text-white/80 hover:bg-white/5 border-t border-white/10"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </div>
        ) : null}

        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="w-full flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/5 text-left"
        >
          <div className="w-9 h-9 rounded-full overflow-hidden bg-brand-blue/80 text-white flex items-center justify-center text-sm font-semibold shrink-0">
            {user?.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={resolveAvatarUrl(user.avatar_url) ?? ""}
                alt="Profile"
                className="w-full h-full object-cover"
              />
            ) : (
              <span>{(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}</span>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-white font-medium truncate">
              {user?.display_name || "—"}
            </p>
            <p className="text-xs text-white/50 truncate">{user?.email || ""}</p>
          </div>
          <Activity size={14} className="text-white/40 shrink-0" strokeWidth={2} />
        </button>
      </div>
    </>
  );

  return (
    <>
      {/* Mobile top bar */}
      <div className="md:hidden h-14 flex items-center justify-between px-4 bg-brand-dark text-white">
        <Logo variant="light" size="sm" />
        <button
          aria-label="Open menu"
          onClick={() => setMobileOpen(true)}
          className="text-white text-2xl leading-none"
        >
          ≡
        </button>
      </div>

      {/* Desktop sidebar */}
      <aside
        className="hidden md:flex md:flex-col md:fixed md:inset-y-0 md:left-0 md:w-60 text-white"
        style={{ backgroundColor: "var(--brand-dark)" }}
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
            style={{ backgroundColor: "var(--brand-dark)" }}
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
