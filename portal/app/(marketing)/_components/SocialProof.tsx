"use client";

// "Works with the tools you already use" — 14 provider logos presented as
// floating white tiles on a 3D plane that tilts ±10–12° with the cursor.
// Each tile casts a separate elliptical ground shadow that stays on the
// floor when the tile lifts on hover, so the scene reads like pieces
// hovering above a surface rather than glued-on decals.
//
// Three-tier depth:
//   • Tier 1 (z +60..+90, size 70–78) — 5 foreground tiles
//   • Tier 2 (z +20..+45, size 54–62) — 5 midground tiles
//   • Tier 3 (z -40..-20, size 38–46) — 4 background tiles
// Positions are hand-placed and committed so the composition never
// shuffles between reloads. Below 768px the plane is replaced with the
// plain 7×2 grayscale grid — parallax on touch is finicky and the static
// grid reads perfectly well on small screens.

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import Image from "next/image";

type Tier = 1 | 2 | 3;
type Provider = {
  slug: string;
  name: string;
  tier: Tier;
  x: number;
  y: number;
  size: number;
  z: number;
  delay: number;
  duration: number;
};

// Coordinates are the CENTRE of each tile on a 720×320 stage.
const PROVIDERS: Provider[] = [
  // Tier 1 — foreground
  { slug: "google",     name: "Google AI",  tier: 1, x: 360, y:  75, size: 78, z:  88, delay: 0.6, duration: 4.2 },
  { slug: "openai",     name: "OpenAI",     tier: 1, x: 115, y: 160, size: 74, z:  75, delay: 0.0, duration: 4.6 },
  { slug: "anthropic",  name: "Anthropic",  tier: 1, x: 600, y: 170, size: 76, z:  80, delay: 1.1, duration: 5.0 },
  { slug: "mistral",    name: "Mistral",    tier: 1, x: 355, y: 245, size: 72, z:  65, delay: 1.8, duration: 5.4 },
  { slug: "groq",       name: "Groq",       tier: 1, x: 530, y:  65, size: 70, z:  60, delay: 0.3, duration: 4.8 },
  // Tier 2 — midground
  { slug: "deepseek",   name: "DeepSeek",   tier: 2, x:  45, y:  75, size: 60, z:  40, delay: 1.4, duration: 5.2 },
  { slug: "openrouter", name: "OpenRouter", tier: 2, x:  45, y: 245, size: 62, z:  45, delay: 0.9, duration: 3.8 },
  { slug: "perplexity", name: "Perplexity", tier: 2, x: 235, y: 190, size: 56, z:  30, delay: 1.6, duration: 4.4 },
  { slug: "cohere",     name: "Cohere",     tier: 2, x: 660, y: 250, size: 58, z:  20, delay: 0.4, duration: 5.5 },
  { slug: "xai",        name: "xAI",        tier: 2, x: 675, y:  80, size: 54, z:  25, delay: 1.3, duration: 3.6 },
  // Tier 3 — background
  { slug: "kimi",       name: "Kimi",       tier: 3, x: 160, y:  30, size: 40, z: -25, delay: 0.2, duration: 5.3 },
  { slug: "qwen",       name: "Qwen",       tier: 3, x: 455, y:  30, size: 42, z: -35, delay: 0.7, duration: 4.9 },
  { slug: "minimax",    name: "Minimax",    tier: 3, x: 155, y: 290, size: 38, z: -40, delay: 1.9, duration: 4.1 },
  { slug: "zai",        name: "Z.AI",       tier: 3, x: 470, y: 295, size: 46, z: -20, delay: 0.8, duration: 4.7 },
];

function Tile({ p }: { p: Provider }) {
  const logoSize = Math.round(p.size * 0.55);
  // Shadow alpha + scale derive from z so background tiles read denser
  // and foreground tiles cast lighter, more spread-out ground contacts.
  const shadowAlpha = Math.max(0.08, 0.22 - p.z * 0.001);
  const shadowScale = Math.max(0.7, 1 + p.z / 260);

  const wrapStyle: CSSProperties = {
    left: `${p.x - p.size / 2}px`,
    top: `${p.y - p.size / 2}px`,
    width: `${p.size}px`,
    height: `${p.size}px`,
    ["--s" as string]: `${p.size}px`,
    ["--tz" as string]: `${p.z}px`,
    ["--shadow-alpha" as string]: shadowAlpha.toFixed(3),
    ["--shadow-scale" as string]: shadowScale.toFixed(3),
  };
  const tileStyle: CSSProperties = {
    animationDelay: `${p.delay}s`,
    animationDuration: `${p.duration}s`,
  };

  return (
    <div className="tile-wrap" style={wrapStyle}>
      <div className="tile-shadow" aria-hidden="true" />
      <div className="tile-lift">
        <div className="tile" style={tileStyle}>
          <Image
            src={`/providers/${p.slug}.png`}
            alt={p.name}
            width={logoSize}
            height={logoSize}
            className="tile-logo"
          />
        </div>
      </div>
      <span className="tile-tooltip" role="tooltip">
        {p.name}
      </span>
    </div>
  );
}

export function SocialProof() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ rx: 0, ry: 0 });
  const [hovered, setHovered] = useState(false);
  // Parallax is driven by JS→transform, so the global reduced-motion
  // rule (which zeroes animation/transition durations) doesn't disable
  // it. Skip tilt updates entirely when the user prefers reduced motion.
  const reducedRef = useRef(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => {
      reducedRef.current = mq.matches;
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const onMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (reducedRef.current) return;
    const el = stageRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width - 0.5;
    const ny = (e.clientY - rect.top) / rect.height - 0.5;
    const cx = Math.max(-0.5, Math.min(0.5, nx));
    const cy = Math.max(-0.5, Math.min(0.5, ny));
    setTilt({ rx: -cy * 20, ry: cx * 24 });
  }, []);

  const onEnter = useCallback(() => setHovered(true), []);
  const onLeave = useCallback(() => {
    setTilt({ rx: 0, ry: 0 });
    setHovered(false);
  }, []);

  return (
    <section className="social">
      <div className="container">
        <div className="social-label reveal">
          Works with the tools you already use
        </div>

        {/* Desktop: 3D parallax stage (≥ 768px). */}
        <div
          ref={stageRef}
          className="social-3d reveal"
          onMouseEnter={onEnter}
          onMouseMove={onMove}
          onMouseLeave={onLeave}
        >
          <div
            className={`social-3d-plane${hovered ? " is-hovered" : ""}`}
            style={{ transform: `rotateX(${tilt.rx}deg) rotateY(${tilt.ry}deg)` }}
          >
            {PROVIDERS.map((p) => (
              <Tile key={p.slug} p={p} />
            ))}
          </div>
        </div>

        {/* Mobile fallback: balanced 7×2 grayscale grid (< 768px). */}
        <div className="logos" role="list">
          {PROVIDERS.map((p) => (
            <div key={p.slug} role="listitem" className="logo-item" title={p.name}>
              <Image
                src={`/providers/${p.slug}.png`}
                alt={p.name}
                width={32}
                height={32}
              />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
