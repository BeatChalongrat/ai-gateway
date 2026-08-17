"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MouseEvent, useEffect, useState } from "react";

import { GET_STARTED_HREF, SIGN_IN_HREF } from "./auth-links";
import { useLang, type Lang } from "./LanguageProvider";

const NAV_OFFSET = 80; // matches scroll-padding-top in marketing.css
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

const NAV_LINKS = [
  { id: "solutions", th: "สิ่งที่เราช่วย", en: "What we do" },
  { id: "how", th: "การทำงาน", en: "How it works" },
  { id: "products", th: "ผลิตภัณฑ์", en: "Products" },
  { id: "security", th: "ความปลอดภัย", en: "Security" },
  { id: "deploy", th: "การติดตั้ง", en: "Deployment" },
] as const;

const copy = {
  th: {
    signIn: "เข้าสู่ระบบ",
    getStarted: "เริ่มต้นใช้งาน",
    openMenu: "เปิดเมนู",
    closeMenu: "ปิดเมนู",
    langLabel: "เลือกภาษา",
  },
  en: {
    signIn: "Sign in",
    getStarted: "Get started",
    openMenu: "Open menu",
    closeMenu: "Close menu",
    langLabel: "Language",
  },
} as const;

export function MarketingNav() {
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();
  const { lang, setLang } = useLang();
  const t = copy[lang];

  // Close the mobile menu whenever the route changes (covers link taps
  // that navigate to a different marketing page).
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

  // On the landing page: animate scroll to the section manually so
  // re-clicks fire and don't get cancelled. On other pages: let the
  // default <a href="/#id"> navigation happen — HashScrollHandler
  // scrolls after the landing page loads.
  function handleAnchorClick(e: MouseEvent<HTMLAnchorElement>, id: string) {
    setMenuOpen(false);
    if (pathname !== "/") return;
    e.preventDefault();
    const el = document.getElementById(id);
    if (!el) return;
    const targetY = el.getBoundingClientRect().top + window.scrollY - NAV_OFFSET;
    smoothScrollTo(targetY);
  }

  const links = (extraClass?: string) =>
    NAV_LINKS.map((l) => (
      <a
        key={l.id}
        href={`/#${l.id}`}
        className={extraClass}
        onClick={(e) => handleAnchorClick(e, l.id)}
      >
        {l[lang]}
      </a>
    ));

  const langSwitch = (
    <div className="lang-switch" role="group" aria-label={t.langLabel}>
      {(["th", "en"] as Lang[]).map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={lang === l}
          onClick={() => setLang(l)}
        >
          {l === "th" ? "ไทย" : "EN"}
        </button>
      ))}
    </div>
  );

  return (
    <header className="marketing-nav">
      <div className="wrap nav-inner">
        <Link href="/" className="brand" aria-label="SRAN AI Gateway">
          <span className="brand-tile">
            <Image src="/sran-icon-white.png" alt="" width={23} height={25} />
          </span>
          <span className="brand-text">
            <span className="brand-name">SRAN</span>
            <span className="brand-tag">AI Gateway</span>
          </span>
        </Link>

        <nav className="nav-links" aria-label={lang === "th" ? "เมนูหลัก" : "Main menu"}>
          {links()}
        </nav>

        <div className="nav-actions">
          {langSwitch}
          <Link href={SIGN_IN_HREF} className="nav-signin">
            {t.signIn}
          </Link>
          <Link href={GET_STARTED_HREF} className="btn btn-small">
            {t.getStarted}
          </Link>
        </div>

        <button
          type="button"
          className="nav-toggle"
          aria-label={menuOpen ? t.closeMenu : t.openMenu}
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
        onClick={() => setMenuOpen(false)}
        aria-hidden="true"
      />
      <div
        id="marketing-mobile-menu"
        className={`nav-mobile${menuOpen ? " open" : ""}`}
      >
        {links()}
        <div className="nav-mobile-cta">
          {langSwitch}
          <Link href={SIGN_IN_HREF} className="btn btn-ghost-dark" onClick={() => setMenuOpen(false)}>
            {t.signIn}
          </Link>
          <Link href={GET_STARTED_HREF} className="btn" onClick={() => setMenuOpen(false)}>
            {t.getStarted}
          </Link>
        </div>
      </div>
    </header>
  );
}
