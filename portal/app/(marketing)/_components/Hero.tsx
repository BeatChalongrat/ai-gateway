"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, Check } from "lucide-react";
import { useState, type MouseEvent, type ReactNode } from "react";

function FrostCard({
  children,
  highlight = false,
  className = "",
}: {
  children: ReactNode;
  highlight?: boolean;
  className?: string;
}) {
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 2;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
    setTilt({ x: x * 4, y: y * 4 });
  };

  const handleMouseLeave = () => setTilt({ x: 0, y: 0 });

  return (
    <div
      className={`group relative rounded-2xl ${className}`}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        background: "rgba(255,255,255,0.8)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        border: "1px solid rgba(139,92,246,0.18)",
        boxShadow: highlight
          ? "inset 0 1px 0 rgba(255,255,255,0.9), inset 0 0 0 1px rgba(168,85,247,0.08), 0 2px 4px rgba(139,92,246,0.12), 0 12px 24px -6px rgba(139,92,246,0.25), 0 32px 60px -16px rgba(139,92,246,0.35)"
          : "inset 0 1px 0 rgba(255,255,255,0.9), 0 1px 2px rgba(139,92,246,0.08), 0 8px 16px -4px rgba(139,92,246,0.12), 0 20px 40px -12px rgba(139,92,246,0.18)",
        transform: `perspective(1000px) rotateY(${tilt.x}deg) rotateX(${-tilt.y}deg)`,
        transition: "transform 0.2s ease-out",
        transformStyle: "preserve-3d",
      }}
    >
      {/* Top gradient hairline */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-4 right-4 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, rgba(139,92,246,0.55), transparent)",
        }}
      />

      {/* Card content */}
      {children}

      {/* Effect 3: vertical scan line (subtle ambient) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl"
      >
        <span className="scan-line-vertical" />
      </div>

      {/* Effect 2: horizontal cyber sweeps (subtle ambient) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl"
      >
        <span className="cyber-line cyber-line-1" />
        <span className="cyber-line cyber-line-2" />
        <span className="cyber-line cyber-line-3" />
      </div>

      {/* Effect 1: corner brackets (static + glow on hover) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-2xl"
      >
        <span className="absolute top-2 left-2 w-3 h-3 border-t-2 border-l-2 border-violet-300/50 rounded-tl-lg transition-all duration-300 group-hover:border-violet-500 group-hover:shadow-[0_0_8px_rgba(139,92,246,0.5)]" />
        <span className="absolute top-2 right-2 w-3 h-3 border-t-2 border-r-2 border-violet-300/50 rounded-tr-lg transition-all duration-300 group-hover:border-violet-500 group-hover:shadow-[0_0_8px_rgba(139,92,246,0.5)]" />
        <span className="absolute bottom-2 left-2 w-3 h-3 border-b-2 border-l-2 border-violet-300/50 rounded-bl-lg transition-all duration-300 group-hover:border-violet-500 group-hover:shadow-[0_0_8px_rgba(139,92,246,0.5)]" />
        <span className="absolute bottom-2 right-2 w-3 h-3 border-b-2 border-r-2 border-violet-300/50 rounded-br-lg transition-all duration-300 group-hover:border-violet-500 group-hover:shadow-[0_0_8px_rgba(139,92,246,0.5)]" />
      </div>

      {/* Effect 4: diagonal glare on hover */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl"
      >
        <span className="card-glare-on-hover" />
      </div>
    </div>
  );
}

function MiniBrowser() {
  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden shadow-[0_2px_6px_-2px_rgba(15,23,42,0.08)]">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-slate-100 bg-slate-50/80">
        <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
        <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
        <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
        <span className="ml-auto text-[9px] font-mono font-semibold text-violet-600 bg-violet-50 border border-violet-200 rounded px-1.5 py-0.5 tracking-wider">
          API
        </span>
      </div>
      <div className="p-4">
        <div className="flex items-start gap-3 mb-3">
          <div className="grid grid-cols-2 gap-1 shrink-0">
            <span className="w-2 h-2 rounded-full bg-violet-500" />
            <span className="w-2 h-2 rounded-full bg-indigo-500" />
            <span className="w-2 h-2 rounded-full bg-fuchsia-500" />
            <span className="w-2 h-2 rounded-full bg-purple-500" />
          </div>
          <div className="flex-1 space-y-1.5 pt-0.5">
            <div className="h-1.5 rounded-full bg-slate-200 w-full" />
            <div className="h-1.5 rounded-full bg-slate-200 w-4/5" />
            <div className="h-1.5 rounded-full bg-slate-100 w-3/5" />
          </div>
        </div>
        <div
          aria-hidden
          className="w-full inline-flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-lg bg-slate-900 text-white text-[11px] font-medium whitespace-nowrap"
        >
          <span className="whitespace-nowrap">Send request</span>
          <svg viewBox="0 0 20 20" width="11" height="11" fill="none" aria-hidden>
            <path
              d="M2 10 L18 3 L11 17 L9 11 Z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
        </div>
      </div>
    </div>
  );
}

function YourAppCard() {
  return (
    <FrostCard className="p-6 h-full flex flex-col">
      <div className="text-center text-[10px] font-medium tracking-[0.28em] text-slate-500 mb-6">
        YOUR APP
      </div>
      <MiniBrowser />
      <div className="mt-4 text-center text-slate-900 font-semibold text-sm">
        Send request
      </div>
      <div className="mt-1 text-center text-violet-600 text-[11px]">
        Use any SDK you already have
      </div>
      <div className="mt-5 pt-5 border-t border-slate-200/60">
        <div
          className="rounded-lg p-3 font-mono text-[10px] text-left leading-relaxed backdrop-blur-sm"
          style={{
            background: "rgba(168, 85, 247, 0.08)",
            border: "1px solid rgba(168, 85, 247, 0.2)",
          }}
        >
          <div className="text-purple-600/90 mb-1 font-medium"># One line change</div>
          <div className="text-slate-500">
            <span className="text-rose-400 font-bold">-</span>{" "}
            <span className="line-through decoration-slate-400">api.openai.com</span>
          </div>
          <div>
            <span className="text-purple-600 font-bold">+</span>{" "}
            <span className="text-purple-700 font-semibold">api.sran.ai</span>
          </div>
        </div>
        <div className="mt-3 flex gap-1.5 justify-center flex-wrap">
          <span className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded-full bg-white border border-slate-200 text-slate-700 font-medium shadow-sm">
            <PythonIcon className="w-3 h-3" />
            Python
          </span>
          <span className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded-full bg-white border border-slate-200 text-slate-700 font-medium shadow-sm">
            <TypeScriptIcon className="w-3 h-3" />
            TypeScript
          </span>
          <span className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded-full bg-white border border-slate-200 text-slate-700 font-medium shadow-sm">
            <GoIcon className="w-4 h-4" />
            Go
          </span>
        </div>
      </div>
    </FrostCard>
  );
}

function FeatureBadge({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <motion.span
      whileHover={{ y: -2, scale: 1.03 }}
      transition={{ type: "spring", stiffness: 380, damping: 22 }}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/70 border border-violet-200/80 text-[11px] font-medium text-slate-700 shadow-[0_1px_2px_rgba(139,92,246,0.12)]"
    >
      <span className="text-violet-600">{icon}</span>
      {label}
    </motion.span>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 14 14" width="12" height="12" fill="none" aria-hidden>
      <path
        d="M7 1.5 L12 3.5 V7 C12 9.8 9.8 11.8 7 12.5 C4.2 11.8 2 9.8 2 7 V3.5 Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RouteIcon() {
  return (
    <svg viewBox="0 0 14 14" width="12" height="12" fill="none" aria-hidden>
      <path
        d="M2 4 H7 A3 3 0 0 1 10 7 A3 3 0 0 0 13 10"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="2" cy="4" r="1.3" fill="currentColor" />
      <circle cx="12.5" cy="10" r="1.3" fill="currentColor" />
    </svg>
  );
}

function LogIcon() {
  return (
    <svg viewBox="0 0 14 14" width="12" height="12" fill="none" aria-hidden>
      <rect
        x="2.5"
        y="2"
        width="9"
        height="10"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <line x1="4.5" y1="5" x2="9.5" y2="5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <line x1="4.5" y1="7.5" x2="9.5" y2="7.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <line x1="4.5" y1="10" x2="7.5" y2="10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function PythonIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden>
      <defs>
        <linearGradient id="pyBlue" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#387EB8" />
          <stop offset="100%" stopColor="#366994" />
        </linearGradient>
        <linearGradient id="pyYellow" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FFE052" />
          <stop offset="100%" stopColor="#FFC331" />
        </linearGradient>
      </defs>
      <path
        fill="url(#pyBlue)"
        d="M11.914 0C5.82 0 6.2 2.656 6.2 2.656v2.75h5.808v.82H3.9s-3.9-.44-3.9 5.62 3.4 5.85 3.4 5.85h2.02v-2.86s-.11-3.39 3.34-3.39h5.76s3.24.05 3.24-3.13V3.2S18.29 0 11.91 0zM8.72 1.84c.58 0 1.05.47 1.05 1.05s-.47 1.05-1.05 1.05-1.05-.47-1.05-1.05.47-1.05 1.05-1.05z"
      />
      <path
        fill="url(#pyYellow)"
        d="M12.086 24c6.094 0 5.714-2.656 5.714-2.656v-2.75h-5.808v-.82h8.108s3.9.44 3.9-5.62S20.6 6.3 20.6 6.3h-2.02v2.86s.11 3.39-3.34 3.39H9.48s-3.24-.05-3.24 3.13v5.12S5.71 24 12.09 24zm3.194-1.84c-.58 0-1.05-.47-1.05-1.05s.47-1.05 1.05-1.05 1.05.47 1.05 1.05-.47 1.05-1.05 1.05z"
      />
    </svg>
  );
}

function TypeScriptIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="5" fill="#3178C6" />
      <text
        x="12"
        y="16.5"
        textAnchor="middle"
        fontSize="10"
        fontWeight="800"
        fill="white"
        style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          letterSpacing: "0.5px",
        }}
      >
        TS
      </text>
    </svg>
  );
}

function GoIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 24" aria-hidden>
      {/* 2 speed lines */}
      <rect x="0" y="9" width="6" height="1.5" fill="#00ADD8" rx="0.5" />
      <rect x="1" y="13.5" width="5" height="1.5" fill="#00ADD8" rx="0.5" />
      {/* GO text — italic bold cyan */}
      <text
        x="8"
        y="19"
        fontSize="17"
        fontWeight="900"
        fill="#00ADD8"
        fontStyle="italic"
        style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          letterSpacing: "-1px",
        }}
      >
        GO
      </text>
    </svg>
  );
}

function GatewayCard() {
  return (
    <FrostCard highlight className="p-7 sm:p-8 relative h-full flex flex-col">
      <div className="text-center text-[10px] font-medium tracking-[0.28em] text-slate-500 mb-6">
        SRAN GATEWAY
      </div>

      <div className="relative flex items-center justify-center mb-5" style={{ height: 200 }}>
        <motion.div
          aria-hidden
          className="absolute rounded-full"
          style={{
            width: 230,
            height: 230,
            background:
              "radial-gradient(circle, rgba(139,92,246,0.45) 0%, rgba(99,102,241,0.18) 45%, transparent 72%)",
            filter: "blur(16px)",
          }}
          animate={{ opacity: [0.45, 0.8, 0.45], scale: [1, 1.08, 1] }}
          transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
        />

        <motion.img
          src="/images/shield.png"
          alt="SRAN security shield"
          width={176}
          height={176}
          draggable={false}
          className="relative w-44 h-44 object-contain select-none"
          style={{
            filter: "drop-shadow(0 14px 22px rgba(109,40,217,0.45))",
            mixBlendMode: "multiply",
          }}
          animate={{ y: [0, -4, 0] }}
          transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
        />

        <motion.div
          aria-hidden
          className="absolute"
          style={{
            left: "calc(50% + 86px)",
            top: "calc(50% - 6px)",
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "#fff",
            boxShadow: "0 0 14px rgba(255,255,255,0.95), 0 0 4px #c084fc",
          }}
          animate={{ opacity: [0, 1, 0], scale: [0.6, 1, 0.6] }}
          transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      <h2 className="text-center text-xl sm:text-2xl font-bold text-slate-900 leading-[1.15] tracking-tight whitespace-nowrap">
        Route<span className="text-violet-600"> · </span>Protect<span className="text-violet-600"> · </span>Track
      </h2>
      <p className="mt-2 text-center text-violet-600 text-sm font-medium">
        Secure routing for every request
      </p>

      <div className="mt-5 flex flex-wrap gap-2 justify-center">
        <FeatureBadge icon={<ShieldIcon />} label="DLP" />
        <FeatureBadge icon={<RouteIcon />} label="Smart route" />
        <FeatureBadge icon={<LogIcon />} label="Logs & billing" />
      </div>

      <div
        aria-hidden
        className="absolute left-1/2 pointer-events-none"
        style={{ bottom: -18, width: 280, height: 40, transform: "translateX(-50%)" }}
      >
        {[0, 1, 2, 3].map((i) => (
          <motion.div
            key={i}
            className="absolute left-1/2 top-1/2 rounded-full"
            style={{
              width: 220 - i * 44,
              height: 14 - i * 2.5,
              border: "1px solid rgba(139,92,246,0.3)",
              transform: "translate(-50%, -50%)",
            }}
            animate={{ opacity: [0.15, 0.45, 0.15] }}
            transition={{
              duration: 2.8,
              repeat: Infinity,
              ease: "easeInOut",
              delay: i * 0.18,
            }}
          />
        ))}
      </div>
    </FrostCard>
  );
}

function ModelIntelCard() {
  return (
    <FrostCard className="p-6 h-full flex flex-col">
      <div className="text-center text-[10px] font-medium tracking-[0.28em] text-slate-500 mb-6">
        MODEL INTELLIGENCE
      </div>

      <div className="relative flex items-center justify-center mb-4" style={{ height: 190 }}>
        <motion.div
          aria-hidden
          className="absolute rounded-full"
          style={{
            width: 210,
            height: 210,
            background:
              "radial-gradient(circle, rgba(139,92,246,0.38) 0%, rgba(99,102,241,0.12) 50%, transparent 75%)",
            filter: "blur(14px)",
          }}
          animate={{ opacity: [0.4, 0.7, 0.4], scale: [1, 1.06, 1] }}
          transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut" }}
        />

        <motion.img
          src="/images/brain.png"
          alt="AI model intelligence"
          width={176}
          height={176}
          draggable={false}
          className="relative w-44 h-44 object-contain select-none"
          style={{
            filter: "drop-shadow(0 10px 18px rgba(124,58,237,0.32))",
            mixBlendMode: "multiply",
          }}
          animate={{ y: [0, -3, 0], scale: [1, 1.015, 1] }}
          transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
        />

        <motion.div
          aria-hidden
          className="absolute left-1/2 top-1/2"
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: "#fff",
            boxShadow: "0 0 10px rgba(255,255,255,0.95), 0 0 3px #c084fc",
          }}
          animate={{
            x: [-20, 30, 10, -25, -20],
            y: [-30, -10, 20, 10, -30],
            opacity: [0.8, 1, 0.9, 1, 0.8],
          }}
          transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
        />
      </div>

      <div className="flex gap-1.5 flex-wrap justify-center">
        {["Reasoning", "Vision", "Fast"].map((t) => (
          <span
            key={t}
            className="text-[10px] font-medium px-2 py-1 rounded-full bg-violet-50/80 border border-violet-200/70 text-violet-700"
          >
            {t}
          </span>
        ))}
      </div>

      <div className="mt-4 text-center">
        <div className="text-[12px] font-semibold text-slate-900">14 providers</div>
        <div className="mt-0.5 text-[11px] text-slate-500">
          OpenAI · Anthropic · Google ·{" "}
          <span className="text-violet-600 font-semibold">+11</span>
        </div>
      </div>
      <div className="mt-5 pt-5 border-t border-slate-200/60 space-y-2">
        <div className="flex items-center justify-center gap-2 text-[11px]">
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
          </span>
          <span className="text-slate-600 font-medium">All providers online</span>
        </div>
        <div className="flex items-center justify-center gap-1.5 text-[10px] text-slate-500">
          <svg className="w-3 h-3 text-violet-500" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
          </svg>
          Auto-failover ·{" "}
          <span className="text-slate-700 font-semibold">&lt;50ms</span> overhead
        </div>
      </div>
    </FrostCard>
  );
}

function Connector() {
  return (
    <div className="relative h-[2px] w-full overflow-hidden" aria-hidden>
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(90deg, transparent 0%, rgba(168,85,247,0.3) 20%, rgba(168,85,247,0.8) 50%, rgba(168,85,247,0.3) 80%, transparent 100%)",
          backgroundSize: "200% 100%",
          animation: "flow-right 2s linear infinite",
        }}
      />
    </div>
  );
}

export function Hero() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-b from-white via-slate-50 to-white">
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 65% 55% at 50% 85%, rgba(139,92,246,0.08), transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage:
            "linear-gradient(rgba(15,23,42,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.05) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          opacity: 0.4,
          WebkitMaskImage:
            "radial-gradient(ellipse 75% 65% at 50% 50%, #000 25%, transparent 90%)",
          maskImage:
            "radial-gradient(ellipse 75% 65% at 50% 50%, #000 25%, transparent 90%)",
        }}
      />

      <div
        className="container relative"
        style={{ zIndex: 1, paddingTop: 72, paddingBottom: 96 }}
      >
        {/* Text hero — uses CSS .anim .d1..d5 keyframes from marketing.css */}
        <div className="text-center mx-auto anim d1" style={{ maxWidth: 780, marginBottom: 72 }}>
          <div
            className="inline-flex items-center gap-2 anim d1"
            style={{
              padding: "6px 14px 6px 10px",
              borderRadius: 999,
              background: "rgba(255,255,255,0.7)",
              border: "1px solid rgba(203,213,225,0.6)",
              fontSize: 13,
              color: "#334155",
              backdropFilter: "blur(8px)",
              WebkitBackdropFilter: "blur(8px)",
              marginBottom: 28,
              boxShadow: "0 4px 16px -6px rgba(15,23,42,0.08)",
            }}
          >
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#10b981",
                boxShadow: "0 0 10px #10b981, 0 0 2px #10b981",
              }}
            />
            <strong style={{ color: "#0f172a", fontWeight: 600 }}>New:</strong>
            <span>Semantic routing cuts AI costs by 38%</span>
          </div>

          <h1
            className="anim d2"
            style={{
              fontSize: "clamp(40px, 6vw, 64px)",
              lineHeight: 1.08,
              letterSpacing: "-0.035em",
              fontWeight: 600,
              marginBottom: 20,
              color: "#0f172a",
            }}
          >
            AI infrastructure,
            <br />
            <span
              style={{
                backgroundImage:
                  "linear-gradient(90deg, #0f172a 0%, #6366f1 45%, #8b5cf6 65%, #0f172a 100%)",
                backgroundSize: "200% 100%",
                WebkitBackgroundClip: "text",
                backgroundClip: "text",
                color: "transparent",
                display: "inline-block",
              }}
            >
              simplified.
            </span>
          </h1>

          <p
            className="anim d3"
            style={{
              fontSize: 18,
              lineHeight: 1.55,
              color: "#475569",
              maxWidth: 620,
              margin: "0 auto 32px",
              fontWeight: 400,
            }}
          >
            One endpoint for every AI model. Automatic cost savings, built-in data
            protection, and analytics that make sense. No rewrite required.
          </p>

          <div
            className="flex justify-center anim d4"
            style={{ gap: 10, marginBottom: 20 }}
          >
            <Link
              href="/signup"
              className="btn btn-lg brand-cta"
              style={{
                border: "1px solid #1a1f36",
                boxShadow: "0 10px 24px -8px rgba(26,31,54,0.35)",
                fontWeight: 500,
                color: "#ffffff",
              }}
            >
              Start free <span className="arrow" aria-hidden><ArrowRight size={16} /></span>
            </Link>
            <a
              href="#how"
              className="btn btn-lg"
              style={{
                background: "rgba(255,255,255,0.75)",
                color: "#0f172a",
                border: "1px solid rgba(148,163,184,0.45)",
                backdropFilter: "blur(8px)",
                WebkitBackdropFilter: "blur(8px)",
              }}
            >
              See how it works
            </a>
          </div>

          <div
            className="flex justify-center flex-wrap anim d5"
            style={{
              gap: 20,
              fontSize: 13,
              color: "#64748b",
            }}
          >
            <span style={{ display: "inline-flex", alignItems: "center" }}>
              <Check size={14} style={{ color: "#10b981", marginRight: 6 }} />
              Free 200K tokens
            </span>
            <span style={{ display: "inline-flex", alignItems: "center" }}>
              <Check size={14} style={{ color: "#10b981", marginRight: 6 }} />
              No credit card
            </span>
            <span style={{ display: "inline-flex", alignItems: "center" }}>
              <Check size={14} style={{ color: "#10b981", marginRight: 6 }} />
              5-minute setup
            </span>
          </div>
        </div>

        {/* Outer frame wraps 3-card grid — centers at mx-auto max-w-5xl within container */}
        <div
          className="relative mx-auto max-w-5xl rounded-[32px] border border-violet-200/50 bg-white/40 backdrop-blur-sm p-6 sm:p-8"
          style={{
            boxShadow:
              "inset 0 1px 0 rgba(255,255,255,0.8), 0 20px 60px -20px rgba(139,92,246,0.2)",
          }}
        >
          {/* 3-card grid — CSS .anim keyframe (bulletproof against framer-motion hydration failures) */}
          <div className="grid items-stretch gap-4 lg:gap-0 grid-cols-1 lg:grid-cols-[minmax(200px,1fr)_48px_minmax(320px,1.4fr)_48px_minmax(200px,1fr)]">
            <div className="anim" style={{ animationDelay: "0.55s" }}>
              <YourAppCard />
            </div>

            <div className="hidden lg:flex items-center justify-center w-full h-full">
              <Connector />
            </div>

            <div className="anim" style={{ animationDelay: "0.7s" }}>
              <GatewayCard />
            </div>

            <div className="hidden lg:flex items-center justify-center w-full h-full">
              <Connector />
            </div>

            <div className="anim" style={{ animationDelay: "0.85s" }}>
              <ModelIntelCard />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
