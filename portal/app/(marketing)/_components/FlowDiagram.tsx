"use client";

import { useEffect, useRef } from "react";

// easeOutQuart matches the reference HTML — gives a fast initial
// run-up that decelerates toward the target.
const easeOutQuart = (t: number): number => 1 - Math.pow(1 - t, 4);

function animateCount(el: HTMLElement) {
  const target = parseFloat(el.dataset.count ?? "0");
  const suffix = el.dataset.suffix ?? "";
  const duration = 1800;
  const hasDecimal = target % 1 !== 0;
  const start = performance.now();

  const tick = (now: number) => {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    const value = target * easeOutQuart(progress);
    el.textContent =
      (hasDecimal ? value.toFixed(2) : Math.floor(value).toString()) + suffix;
    if (progress < 1) {
      requestAnimationFrame(tick);
    } else {
      el.textContent = (hasDecimal ? target.toFixed(2) : target.toString()) + suffix;
    }
  };
  requestAnimationFrame(tick);
}

export function FlowDiagram() {
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = wrapRef.current;
    if (!root) return;

    // Honor reduced-motion: skip the count-up and just render the
    // final value. The CSS layer already neutralizes other animations.
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const targets = root.querySelectorAll<HTMLElement>("[data-count]");

    if (reduced) {
      targets.forEach((el) => {
        const v = parseFloat(el.dataset.count ?? "0");
        const hasDecimal = v % 1 !== 0;
        el.textContent =
          (hasDecimal ? v.toFixed(2) : v.toString()) + (el.dataset.suffix ?? "");
      });
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCount(entry.target as HTMLElement);
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.5 }
    );

    targets.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="flow-wrap" ref={wrapRef}>
      <div className="flow-row">
        <div className="flow-node left">
          <div className="node-label">Your app</div>
          <div className="node-icon">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="3" fill="currentColor" opacity="0.12" />
              <path d="M9 9l-3 3 3 3M15 9l3 3-3 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="node-title">Send request</div>
          <div className="node-desc">Use any SDK you already have</div>
        </div>
        <div className="flow-arrow" aria-hidden="true">
          <div className="pulse-dot" />
        </div>
        <div className="flow-node center">
          <div className="node-label">SRAN Gateway</div>
          <div className="node-icon">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true">
              <path d="M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6l9-4z" fill="#fff" fillOpacity="0.25" />
              <path d="M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6l9-4z" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
              <path d="M8.5 12l2.5 2.5L16 10" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="node-title">Route · Protect · Track</div>
          <div className="node-desc">Every request secured and optimized</div>
          <div className="node-features">
            <span>✓ DLP scan</span>
            <span>✓ Smart route</span>
            <span>✓ Log &amp; bill</span>
          </div>
        </div>
        <div className="flow-arrow" aria-hidden="true">
          <div className="pulse-dot" />
        </div>
        <div className="flow-node right">
          <div className="node-label">14 providers</div>
          <div className="node-icon">
            <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.2" />
              <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
              <circle cx="5" cy="5" r="2" fill="currentColor" />
              <circle cx="19" cy="5" r="2" fill="currentColor" />
              <circle cx="5" cy="19" r="2" fill="currentColor" />
              <circle cx="19" cy="19" r="2" fill="currentColor" />
              <path d="M7 7l3 3M14 10l3-3M7 17l3-3M14 14l3 3" stroke="currentColor" strokeWidth="1.5" opacity="0.5" />
            </svg>
          </div>
          <div className="node-title">Best model wins</div>
          <div className="node-desc">OpenAI · Anthropic · Google · more</div>
        </div>
      </div>

      <div className="flow-stats">
        <div className="flow-stat">
          <div className="flow-stat-val" data-count="38" data-suffix="% ↓">
            0%
          </div>
          <div className="flow-stat-lbl">Average cost reduction</div>
        </div>
        <div className="flow-stat">
          <div className="flow-stat-val" data-count="247" data-suffix="ms">
            0ms
          </div>
          <div className="flow-stat-lbl">Median latency overhead</div>
        </div>
        <div className="flow-stat">
          <div className="flow-stat-val" data-count="99.98" data-suffix="%">
            0%
          </div>
          <div className="flow-stat-lbl">Uptime last 90 days</div>
        </div>
      </div>
    </div>
  );
}
