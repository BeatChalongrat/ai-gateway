"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MouseEvent, useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";

import LogoTile from "@/app/_shared/LogoTile";

const NAV_OFFSET = 80; // matches scroll-padding-top in globals.css
const SCROLL_DURATION = 600; // ms

// Manual rAF scroll with ease-in-out cubic. We avoid both
// scrollIntoView({ behavior: "smooth" }) and CSS scroll-behavior:
// smooth — Chrome cancels them mid-animation when other scroll work
// is happening (IntersectionObserver reveals on the landing page).
// Instant window.scrollTo is reliable, so we drive the easing in JS.
function smoothScrollTo(targetY: number, duration = SCROLL_DURATION) {
  const startY = window.scrollY;
  const distance = targetY - startY;
  if (Math.abs(distance) < 2) return;

  const startTime = performance.now();
  const easeInOutCubic = (t: number) =>
    t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

  const step = (now: number) => {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    window.scrollTo(0, startY + distance * easeInOutCubic(progress));
    if (progress < 1) requestAnimationFrame(step);
  };

  requestAnimationFrame(step);
}

export function MarketingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Close the mobile menu whenever the route changes (covers link taps that
  // navigate to a different marketing page).
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  // Lock background scroll while the mobile menu is open.
  useEffect(() => {
    if (!menuOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [menuOpen]);

  // On landing: animate scroll to #features manually so re-clicks fire
  // and don't get cancelled. On other pages: let the default <a>
  // navigation to /#features happen and the browser will jump to the
  // anchor on landing page load.
  function handleProductClick(e: MouseEvent<HTMLAnchorElement>) {
    if (pathname !== "/") return;
    e.preventDefault();
    const el = document.getElementById("features");
    if (!el) return;
    const targetY = el.getBoundingClientRect().top + window.scrollY - NAV_OFFSET;
    smoothScrollTo(targetY);
  }

  const closeMenu = () => setMenuOpen(false);

  return (
    <nav className={`marketing-nav${scrolled ? " scrolled" : ""}`} aria-label="Primary">
      <div className="container nav-inner">
        <Link href="/" className="brand" aria-label="SRAN — AI Gateway home">
          <LogoTile size={36} radius={8} />
          <span className="brand-text">
            <span className="brand-name">SRAN</span>
            <span className="brand-tag">AI GATEWAY</span>
          </span>
        </Link>
        <div className="nav-links">
          <a
            href="/#features"
            onClick={handleProductClick}
            className={pathname === "/" ? "is-active" : undefined}
          >
            Product
          </a>
          <a
            href="/security"
            className={pathname.startsWith("/security") ? "is-active" : undefined}
          >
            Security
          </a>
          <a
            href="/pricing"
            className={pathname.startsWith("/pricing") ? "is-active" : undefined}
          >
            Pricing
          </a>
          <a
            href="/docs"
            className={pathname.startsWith("/docs") ? "is-active" : undefined}
          >
            Docs
          </a>
        </div>
        <div className="nav-cta">
          <Link href="/login" className="btn btn-ghost">
            Sign in
          </Link>
          <Link
            href="/signup"
            className="btn brand-cta"
            style={{ fontWeight: 500, color: "#ffffff" }}
          >
            Get started <span className="arrow" aria-hidden="true"><ArrowRight size={16} /></span>
          </Link>
        </div>

        <button
          type="button"
          className="nav-toggle"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
          aria-controls="marketing-mobile-menu"
          onClick={() => setMenuOpen((v) => !v)}
        >
          {menuOpen ? (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
          ) : (
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></svg>
          )}
        </button>
      </div>

      {/* Mobile menu (<=900px) — backdrop + slide-down panel. */}
      <div
        className={`nav-backdrop${menuOpen ? " open" : ""}`}
        onClick={closeMenu}
        aria-hidden="true"
      />
      <div
        id="marketing-mobile-menu"
        className={`nav-mobile${menuOpen ? " open" : ""}`}
      >
        <a
          href="/#features"
          onClick={(e) => {
            handleProductClick(e);
            closeMenu();
          }}
          className={pathname === "/" ? "is-active" : undefined}
        >
          Product
        </a>
        <a
          href="/security"
          onClick={closeMenu}
          className={pathname.startsWith("/security") ? "is-active" : undefined}
        >
          Security
        </a>
        <a
          href="/pricing"
          onClick={closeMenu}
          className={pathname.startsWith("/pricing") ? "is-active" : undefined}
        >
          Pricing
        </a>
        <a
          href="/docs"
          onClick={closeMenu}
          className={pathname.startsWith("/docs") ? "is-active" : undefined}
        >
          Docs
        </a>
        <div className="nav-mobile-cta">
          <Link href="/login" className="btn btn-ghost" onClick={closeMenu}>
            Sign in
          </Link>
          <Link
            href="/signup"
            className="btn brand-cta"
            style={{ fontWeight: 500, color: "#ffffff" }}
            onClick={closeMenu}
          >
            Get started <span className="arrow" aria-hidden="true"><ArrowRight size={16} /></span>
          </Link>
        </div>
      </div>
    </nav>
  );
}
