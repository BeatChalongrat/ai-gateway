"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

export type Stat = { value: ReactNode; label: string };

export type Slide = {
  icon?: ReactNode;
  headlineTop: string;
  headlineBottom: string;
  subtext: string;
  stats: [Stat, Stat, Stat];
};

type Props = {
  slides: Slide[];
  autoAdvanceMs?: number;
};

export default function VisualPanel({ slides, autoAdvanceMs = 5000 }: Props) {
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    if (slides.length <= 1 || autoAdvanceMs === 0) return;
    // Honor prefers-reduced-motion by staying on the first slide.
    const prefersReduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    if (prefersReduced) return;
    const id = setInterval(
      () => setCurrent((i) => (i + 1) % slides.length),
      autoAdvanceMs
    );
    return () => clearInterval(id);
  }, [slides.length, autoAdvanceMs]);

  return (
    <aside className="av-visual">
      <div className="av-inner">
        {slides.map((s, i) => (
          <div
            key={i}
            className={`av-slide${i === current ? " is-active" : ""}`}
            aria-hidden={i !== current}
          >
            <div className="av-big-logo">
              {s.icon ?? (
                <Link href="/" aria-label="SRAN — back to home" className="av-logo-link">
                  <Image
                    src="/sran-logo.png"
                    alt="SRAN"
                    width={120}
                    height={120}
                    priority={i === 0}
                  />
                </Link>
              )}
            </div>
            <h2 className="av-headline">
              {s.headlineTop}
              <br />
              <span className="av-grad">{s.headlineBottom}</span>
            </h2>
            <p className="av-subtext">{s.subtext}</p>
            <div className="av-stats-card">
              {s.stats.map((stat, j) => (
                <div key={j} className="av-stat">
                  <div className="av-stat-v">{stat.value}</div>
                  <div className="av-stat-l">{stat.label}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {slides.length > 1 ? (
        <div className="av-dots" role="tablist" aria-label="Slideshow">
          {slides.map((_, i) => (
            <button
              key={i}
              type="button"
              className={`av-dot${i === current ? " is-active" : ""}`}
              onClick={() => setCurrent(i)}
              aria-label={`Go to slide ${i + 1}`}
              role="tab"
              aria-selected={i === current}
            />
          ))}
        </div>
      ) : null}

      <div className="av-copy">© 2026 SRAN</div>
    </aside>
  );
}
