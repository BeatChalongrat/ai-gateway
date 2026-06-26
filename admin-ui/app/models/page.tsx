"use client";

import { useState, useMemo } from "react";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import { useApi } from "../../lib/useApi";
import { ThemeToggle } from "../../components/ThemeToggle";

/* ── Types ────────────────────────────────────────────────────────── */

interface ModelItem {
  model: string;
  provider: string;
  supports_streaming: boolean;
  cost_tier: string;
  latency_tier: string;
}

/* ── Provider brand colors ────────────────────────────────────────── */

const BRAND: Record<string, string> = {
  openai: "#10a37f", anthropic: "#e07a2f", minimax: "#7c5cbf",
  kimi: "#0891b2", qwen: "#3b6cf6", zai: "#db2777", "z.ai": "#db2777",
  openrouter: "#64748b",
};
function brand(p: string) { return BRAND[p.toLowerCase()] || "#64748b"; }

/* ── Model enrichment (client-side known metadata) ────────────────── */

const META: Record<string, { ctx: string; ctxK: number; caps: string[] }> = {
  "gpt-4o":         { ctx: "128K", ctxK: 128, caps: ["vision","tools","code"] },
  "gpt-4o-mini":    { ctx: "128K", ctxK: 128, caps: ["vision","tools","code"] },
  "gpt-4-turbo":    { ctx: "128K", ctxK: 128, caps: ["vision","tools"] },
  "gpt-3.5-turbo":  { ctx: "16K",  ctxK: 16,  caps: ["tools"] },
  "claude-3-5-sonnet-20241022": { ctx: "200K", ctxK: 200, caps: ["vision","tools","code"] },
  "claude-3-opus-20240229":     { ctx: "200K", ctxK: 200, caps: ["vision","tools","code"] },
  "claude-3-haiku-20240307":    { ctx: "200K", ctxK: 200, caps: ["vision","tools"] },
  "abab6.5s-chat":  { ctx: "245K", ctxK: 245, caps: ["tools"] },
  "abab5.5-chat":   { ctx: "32K",  ctxK: 32,  caps: ["tools"] },
  "qwen-turbo":     { ctx: "128K", ctxK: 128, caps: ["tools","code"] },
  "qwen-plus":      { ctx: "128K", ctxK: 128, caps: ["tools","code"] },
  "qwen-max":       { ctx: "32K",  ctxK: 32,  caps: ["tools","code"] },
  "moonshot-v1-8k":   { ctx: "8K",   ctxK: 8,   caps: ["tools"] },
  "moonshot-v1-32k":  { ctx: "32K",  ctxK: 32,  caps: ["tools"] },
  "moonshot-v1-128k": { ctx: "128K", ctxK: 128, caps: ["tools"] },
  "yi-large":  { ctx: "32K", ctxK: 32, caps: ["tools"] },
  "yi-medium": { ctx: "16K", ctxK: 16, caps: ["tools"] },
};
function getMeta(m: string) {
  if (META[m]) return META[m];
  for (const k of Object.keys(META)) { if (m.startsWith(k)) return META[k]; }
  return null;
}

/* ── Provider logo with fallback ───────────────────────────────────── */

function ProviderLogo({ provider, size = 36 }: { provider: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const c = brand(provider);
  const src = `/providers/${provider.toLowerCase()}.png`;
  const initials = provider.slice(0, 2).toUpperCase();

  return (
    <div style={{
      width: size, height: size, borderRadius: 6, flexShrink: 0,
      background: `${c}1A`,
      display: "flex", alignItems: "center", justifyContent: "center",
      overflow: "hidden",
    }}>
      {!failed ? (
        <img
          src={src}
          alt={provider}
          onError={() => setFailed(true)}
          style={{
            width: size - 8, height: size - 8,
            objectFit: "contain",
          }}
        />
      ) : (
        <span style={{
          fontSize: size * 0.3, fontWeight: 700,
          color: c, lineHeight: 1,
          textTransform: "uppercase",
        }}>{initials}</span>
      )}
    </div>
  );
}

/* ── Capability tag colors ────────────────────────────────────────── */

const CAP: Record<string, { color: string; bg: string }> = {
  vision: { color: "#7c3aed", bg: "rgba(124,58,237,0.07)" },
  tools:  { color: "#0891b2", bg: "rgba(8,145,178,0.07)" },
  code:   { color: "#ca8a04", bg: "rgba(202,138,4,0.07)" },
};

/* ── Tier badge styles ────────────────────────────────────────────── */

const TIER: Record<string, { color: string; bg: string }> = {
  budget:   { color: "#059669", bg: "rgba(5,150,105,0.07)" },
  standard: { color: "#2563eb", bg: "rgba(37,99,235,0.06)" },
  premium:  { color: "#d97706", bg: "rgba(217,119,6,0.07)" },
  fast:     { color: "#059669", bg: "rgba(5,150,105,0.07)" },
  medium:   { color: "#2563eb", bg: "rgba(37,99,235,0.06)" },
  slow:     { color: "#d97706", bg: "rgba(217,119,6,0.07)" },
};

/* ── Scoped CSS ───────────────────────────────────────────────────── */

const STYLES = `
.mr-table { width:100%; border-collapse:collapse; }
.mr-table th {
  text-align:left; padding:0.5rem 0.875rem; font-weight:500; font-size:0.6875rem;
  text-transform:uppercase; letter-spacing:0.06em; color:var(--text-secondary); font-weight:600;
  border-bottom:1px solid var(--border-default); background:var(--bg-surface-2); white-space:nowrap;
  position:sticky; top:0; z-index:1;
}
.mr-table td { padding:1.25rem 0.875rem; border-bottom:1px solid var(--border-subtle); vertical-align:middle; color:var(--text-primary); }
.mr-table tbody tr { transition: background 100ms ease; }
.mr-table tbody tr:hover { background: rgba(99,102,241,0.05); }
.mr-table tbody tr:last-child td { border-bottom:none; }
@keyframes mrFadeUp { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
@keyframes mrFadeIn { from{opacity:0} to{opacity:1} }
.mr-stat { animation: mrFadeUp 0.4s ease-out both; }
.mr-row { animation: mrFadeUp 0.35s ease-out both; }
.mr-chip-fade { animation: mrFadeIn 0.4s ease-out both; }
@keyframes mrPulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.08)} }
@keyframes mrRock { 0%,100%{transform:rotate(0deg)} 25%{transform:rotate(8deg)} 75%{transform:rotate(-8deg)} }
@keyframes mrBreathe { 0%,100%{opacity:1} 50%{opacity:0.6} }
/* One-shot on mount (was infinite) — a single attention cue, not perpetual
   motion on a dense data table. */
.mr-icon-pulse svg { animation: mrPulse 2.5s ease-in-out; }
.mr-icon-rock svg { animation: mrRock 3s ease-in-out; }
.mr-icon-breathe svg { animation: mrBreathe 2s ease-in-out; }
`;

/* ── Sub-components ───────────────────────────────────────────────── */

function ContextBar({ ctx, ctxK }: { ctx: string; ctxK: number }) {
  const pct = Math.min(100, (ctxK / 256) * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 100 }}>
      <div style={{ flex: 1, height: 3, borderRadius: 2, background: "var(--bg-elevated)", overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%", borderRadius: 2,
          background: `linear-gradient(90deg, #3b82f6 0%, #60a5fa 100%)`,
        }} />
      </div>
      <span style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: "0.625rem", fontWeight: 600, color: "var(--text-tertiary)", whiteSpace: "nowrap",
      }}>{ctx}</span>
    </div>
  );
}

function CapTag({ cap }: { cap: string }) {
  const s = CAP[cap] || { color: "#64748b", bg: "rgba(100,116,139,0.06)" };
  return (
    <span style={{
      display: "inline-block", padding: "0.0625rem 0.375rem",
      fontSize: "0.625rem", fontWeight: 600, color: s.color, background: s.bg,
      borderRadius: 4, lineHeight: 1.6, textTransform: "capitalize",
    }}>{cap}</span>
  );
}

function TierBadge({ value }: { value: string }) {
  const s = TIER[value] || TIER.standard;
  return (
    <span style={{
      display: "inline-block", padding: "0.0625rem 0.4375rem",
      fontSize: "0.6875rem", fontWeight: 600, color: s.color, background: s.bg,
      borderRadius: 4, lineHeight: 1.5, textTransform: "capitalize",
    }}>{value}</span>
  );
}

function ProviderChip({ name, active, count, onClick }: {
  name: string; active: boolean; count: number; onClick: () => void;
}) {
  const c = brand(name);
  return (
    <button onClick={onClick} style={{
      display: "inline-flex", alignItems: "center", gap: "0.3125rem",
      padding: "0.25rem 0.5rem 0.25rem 0.375rem",
      fontSize: "0.6875rem", fontWeight: active ? 600 : 500,
      borderRadius: 6,
      border: active ? `1.5px solid ${c}` : "1.5px solid transparent",
      background: active ? `${c}22` : "transparent",
      color: active ? c : "var(--text-tertiary)",
      cursor: "pointer", transition: "all 120ms",
      textTransform: "capitalize", whiteSpace: "nowrap",
      fontFamily: "inherit",
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
        background: c, opacity: active ? 1 : 0.5,
      }} />
      {name}
      <span style={{
        fontSize: "0.5625rem", fontWeight: 700, color: active ? c : "var(--text-muted)",
        marginLeft: "0.0625rem",
      }}>{count}</span>
    </button>
  );
}

function StatCard({ value, label, icon, accent, delay = 0, iconClass }: {
  value: string | number; label: string; icon: React.ReactNode; accent: string; delay?: number; iconClass?: string;
}) {
  return (
    <div className="mr-stat" style={{
      animationDelay: `${delay}ms`,
      background: "var(--bg-surface)", borderRadius: 10,
      border: "1px solid var(--border-default)",
      padding: "0.875rem 1rem",
      display: "flex", alignItems: "center", gap: "0.75rem",
      flex: 1, minWidth: 0,
    }}>
      <div className={iconClass} style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: `${accent}0A`, border: `1px solid ${accent}18`,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: accent,
      }}>{icon}</div>
      <div>
        <div style={{
          fontSize: "1.25rem", fontWeight: 700, color: "var(--text-primary)",
          lineHeight: 1, letterSpacing: "-0.03em",
          fontVariantNumeric: "tabular-nums",
        }}>{value}</div>
        <div style={{
          fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
          marginTop: "0.125rem", lineHeight: 1,
        }}>{label}</div>
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

const PAGE_SIZE = 15;

export default function ModelsPage() {
  const { data, loading, error, refetch } = useApi<{ items: ModelItem[] }>("/admin/models");
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [streamingOnly, setStreamingOnly] = useState(false);
  const [view, setView] = useState<"list" | "grid">("list");
  const [page, setPage] = useState(1);

  const items = data?.items ?? [];

  const providers = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((m) => { c[m.provider] = (c[m.provider] || 0) + 1; });
    return Object.entries(c).sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const streamingCount = useMemo(() => items.filter((m) => m.supports_streaming).length, [items]);

  const filtered = useMemo(() => {
    let r = items;
    if (providerFilter) r = r.filter((m) => m.provider === providerFilter);
    if (streamingOnly) r = r.filter((m) => m.supports_streaming);
    if (search) {
      const q = search.toLowerCase();
      r = r.filter((m) =>
        m.model.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q) ||
        m.cost_tier.toLowerCase().includes(q) ||
        m.latency_tier.toLowerCase().includes(q)
      );
    }
    return r;
  }, [items, search, providerFilter, streamingOnly]);

  const paged = useMemo(() => {
    const s = (page - 1) * PAGE_SIZE;
    return filtered.slice(s, s + PAGE_SIZE);
  }, [filtered, page]);

  const isFiltered = !!search || !!providerFilter || streamingOnly;

  function handleSearch(v: string) { setSearch(v); setPage(1); }
  function handleProvider(p: string | null) { setProviderFilter(p); setPage(1); }
  function toggleStream() { setStreamingOnly(!streamingOnly); setPage(1); }

  // SVG icons for stat cards (LayoutGrid, Plug, Radio — distinct from sidebar nav)
  const iconModels = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><path d="M3.27 6.96L12 12.01l8.73-5.05"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>;
  const iconProviders = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 01-4 4h-4a4 4 0 01-4-4V8z"/></svg>;
  const iconStream = <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.4"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.4"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg>;

  return (
    <div style={{ background: "var(--bg-base)", margin: "-1.75rem -2.25rem -2.5rem", padding: "1.75rem 2.25rem 2.5rem", minHeight: "100%" }}>
      <style>{STYLES}</style>

      {/* ── Header ── */}
      <div className="sran-page-header" style={{ marginBottom: "1.25rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 className="sran-page-title">Model Registry</h1>
          <p className="sran-page-subtitle">Capabilities, cost tiers, and latency metadata for all registered models</p>
        </div>
        <ThemeToggle />
      </div>

      {/* ── Stats row ── */}
      {!loading && !error && items.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3" style={{ gap: "0.75rem", marginBottom: "1.25rem" }}>
          <StatCard value={items.length} label="Total Models" icon={iconModels} accent="#3b82f6" delay={0} iconClass="mr-icon-pulse" />
          <StatCard value={providers.length} label="Providers" icon={iconProviders} accent="#8b5cf6" delay={80} iconClass="mr-icon-rock" />
          <StatCard value={streamingCount} label="Streaming" icon={iconStream} accent="#10a37f" delay={160} iconClass="mr-icon-breathe" />
        </div>
      )}

      {/* ── Main registry surface ── */}
      <div style={{
        background: "var(--bg-surface)", borderRadius: 12,
        border: "1px solid var(--border-default)",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)",
        overflow: "hidden",
      }}>
        {loading ? (
          <SkeletonTable rows={10} cols={6} />
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : items.length === 0 ? (
          <EmptyState message="No models registered" icon={<Icon name="box" size={22} />} />
        ) : (
          <>
            {/* ── Toolbar ── */}
            <div className="mr-chip-fade" style={{
              animationDelay: "0.2s",
              padding: "0.625rem 1rem",
              borderBottom: "1px solid #f1f5f9",
              display: "flex", alignItems: "center", gap: "0.5rem",
              flexWrap: "wrap",
            }}>
              {/* Search */}
              <div style={{ position: "relative", flex: "0 1 200px", minWidth: "140px" }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: "absolute", left: "0.5rem", top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}>
                  <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
                <input
                  type="text" value={search}
                  onChange={(e) => handleSearch(e.target.value)}
                  placeholder="Search models..."
                  style={{
                    width: "100%",
                    padding: "0.3125rem 1.5rem 0.3125rem 1.75rem",
                    fontSize: "0.8125rem", fontFamily: "inherit",
                    border: "1.5px solid var(--border-default)", borderRadius: 6,
                    background: "var(--bg-base)", color: "var(--text-primary)", outline: "none",
                    transition: "border-color 120ms, box-shadow 120ms, background 120ms",
                  }}
                  onFocus={(e) => {
                    e.currentTarget.style.borderColor = "var(--accent-primary)";
                    e.currentTarget.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.15)";
                    e.currentTarget.style.background = "var(--bg-surface)";
                  }}
                  onBlur={(e) => {
                    e.currentTarget.style.borderColor = "var(--border-default)";
                    e.currentTarget.style.boxShadow = "none";
                    e.currentTarget.style.background = "var(--bg-base)";
                  }}
                />
                {search && (
                  <button onClick={() => handleSearch("")} style={{
                    position: "absolute", right: "0.375rem", top: "50%", transform: "translateY(-50%)",
                    background: "none", border: "none", cursor: "pointer",
                    color: "var(--text-muted)", fontSize: "0.75rem", padding: 2,
                  }}><Icon name="x" size={12} /></button>
                )}
              </div>

              {/* Divider */}
              <span style={{ width: 1, height: 20, background: "var(--border-default)", flexShrink: 0 }} />

              {/* Provider chips */}
              <div style={{ display: "flex", gap: "0.1875rem", flexWrap: "wrap", alignItems: "center" }}>
                <button onClick={() => handleProvider(null)} style={{
                  display: "inline-flex", alignItems: "center", padding: "0.25rem 0.5rem",
                  fontSize: "0.6875rem", fontWeight: providerFilter === null ? 600 : 500,
                  borderRadius: 6, cursor: "pointer", transition: "all 120ms",
                  border: providerFilter === null ? "1.5px solid var(--accent-primary)" : "1.5px solid transparent",
                  background: providerFilter === null ? "rgba(99,102,241,0.12)" : "transparent",
                  color: providerFilter === null ? "var(--accent-primary)" : "var(--text-tertiary)",
                  fontFamily: "inherit",
                }}>All <span style={{ fontSize: "0.5625rem", fontWeight: 700, marginLeft: 3, color: providerFilter === null ? "var(--accent-primary)" : "var(--text-muted)" }}>{items.length}</span></button>
                {providers.map(([name, count]) => (
                  <ProviderChip
                    key={name} name={name} count={count}
                    active={providerFilter === name}
                    onClick={() => handleProvider(providerFilter === name ? null : name)}
                  />
                ))}
              </div>

              {/* Divider */}
              <span style={{ width: 1, height: 20, background: "var(--border-default)", flexShrink: 0 }} />

              {/* Streaming toggle */}
              <button onClick={toggleStream} style={{
                display: "inline-flex", alignItems: "center", gap: "0.25rem",
                padding: "0.25rem 0.5rem", fontSize: "0.6875rem", fontFamily: "inherit",
                fontWeight: streamingOnly ? 600 : 500, borderRadius: 6, cursor: "pointer",
                border: streamingOnly ? "1.5px solid #10a37f" : "1.5px solid transparent",
                background: streamingOnly ? "rgba(16,163,127,0.06)" : "transparent",
                color: streamingOnly ? "#10a37f" : "#64748b",
                transition: "all 120ms",
              }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                Stream
              </button>

              {/* Spacer + view toggle + count */}
              <span style={{ flex: 1 }} />

              <div style={{ display: "flex", gap: "0.125rem", background: "var(--bg-elevated)", borderRadius: 6, padding: 2 }}>
                {(["list", "grid"] as const).map((v) => (
                  <button key={v} onClick={() => setView(v)} style={{
                    display: "flex", alignItems: "center", justifyContent: "center",
                    width: 28, height: 24, borderRadius: 4, border: "none", cursor: "pointer",
                    background: view === v ? "var(--bg-surface)" : "transparent",
                    boxShadow: view === v ? "0 1px 2px rgba(0,0,0,0.18)" : "none",
                    color: view === v ? "var(--text-primary)" : "var(--text-muted)",
                    transition: "all 100ms",
                  }}>
                    {v === "list" ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
                    )}
                  </button>
                ))}
              </div>

              <span style={{
                fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
                whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums",
              }}>
                {isFiltered ? `${filtered.length} of ${items.length}` : `${items.length} models`}
              </span>
            </div>

            {/* ── Content ── */}
            {filtered.length === 0 ? (
              <div style={{ padding: "3rem 1rem", textAlign: "center" }}>
                <p style={{ fontSize: "0.8125rem", color: "var(--text-tertiary)", margin: "0 0 0.75rem" }}>
                  No models match the current filters
                </p>
                <button onClick={() => { setSearch(""); setProviderFilter(null); setStreamingOnly(false); setPage(1); }}
                  style={{
                    padding: "0.375rem 0.875rem", fontSize: "0.8125rem", fontWeight: 500,
                    borderRadius: 6, border: "1.5px solid var(--border-default)", background: "var(--bg-surface)",
                    color: "var(--text-primary)", cursor: "pointer", fontFamily: "inherit",
                  }}>Clear filters</button>
              </div>
            ) : view === "list" ? (
              <>
                <div style={{ overflowX: "auto" }}>
                  <table className="mr-table" style={{ minWidth: 820 }}>
                    <thead>
                      <tr>
                        <th style={{ paddingLeft: "1.125rem", width: "28%" }}>Model</th>
                        <th style={{ width: "15%" }}>Context</th>
                        <th>Capabilities</th>
                        <th style={{ textAlign: "center", width: 64 }}>Stream</th>
                        <th style={{ width: 84 }}>Cost</th>
                        <th style={{ width: 84 }}>Latency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paged.map((m, i) => {
                        const meta = getMeta(m.model);
                        return (
                          <tr key={m.model} className="mr-row" style={{ animationDelay: `${0.3 + i * 0.03}s` }}>
                            {/* Model identity */}
                            <td style={{ paddingLeft: "1.125rem", paddingTop: "1.25rem", paddingBottom: "1.25rem" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                                <ProviderLogo provider={m.provider} size={36} />
                                <div>
                                  <code style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                    fontSize: "0.8125rem", fontWeight: 600, color: "var(--text-primary)",
                                    letterSpacing: "-0.01em", lineHeight: 1.3,
                                  }}>{m.model}</code>
                                  <div style={{ display: "flex", alignItems: "center", gap: "0.3125rem", marginTop: "0.1rem" }}>
                                    <span style={{
                                      width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                                      background: brand(m.provider),
                                    }} />
                                    <span style={{
                                      fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)",
                                      textTransform: "capitalize", lineHeight: 1,
                                    }}>{m.provider}</span>
                                  </div>
                                </div>
                              </div>
                            </td>
                            {/* Context window */}
                            <td>
                              {meta?.ctx ? (
                                <ContextBar ctx={meta.ctx} ctxK={meta.ctxK} />
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>—</span>
                              )}
                            </td>
                            {/* Capabilities */}
                            <td>
                              {meta?.caps?.length ? (
                                <div style={{ display: "flex", gap: "0.1875rem", flexWrap: "wrap" }}>
                                  {meta.caps.map((c) => <CapTag key={c} cap={c} />)}
                                </div>
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>—</span>
                              )}
                            </td>
                            {/* Stream */}
                            <td style={{ textAlign: "center" }}>
                              {m.supports_streaming ? (
                                <span style={{
                                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                                  width: 22, height: 22, borderRadius: "50%",
                                  background: "rgba(16,163,127,0.08)",
                                }}>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10a37f" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="20 6 9 17 4 12"/>
                                  </svg>
                                </span>
                              ) : (
                                <span style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>—</span>
                              )}
                            </td>
                            {/* Cost */}
                            <td><TierBadge value={m.cost_tier} /></td>
                            {/* Latency */}
                            <td><TierBadge value={m.latency_tier} /></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {filtered.length > PAGE_SIZE && (
                  <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
                )}
              </>
            ) : (
              /* ── Grid view ── */
              <>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 280px), 1fr))",
                  gap: "0.75rem", padding: "1rem",
                }}>
                  {paged.map((m, i) => {
                    const meta = getMeta(m.model);
                    return (
                      <div key={m.model} className="mr-row" style={{
                        animationDelay: `${0.3 + i * 0.03}s`,
                        background: "var(--bg-surface-2)", borderRadius: 10,
                        border: "1px solid var(--border-subtle)",
                        padding: "0.875rem 1rem",
                        display: "flex", flexDirection: "column", gap: "0.5rem",
                        transition: "border-color 150ms, box-shadow 150ms",
                      }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--border-default)"; e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.18)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border-subtle)"; e.currentTarget.style.boxShadow = "none"; }}
                        // Keyboard-focus parity with the mouse hover lift (mirrors providers).
                        onFocus={(e) => { e.currentTarget.style.borderColor = "var(--border-default)"; e.currentTarget.style.boxShadow = "0 2px 8px rgba(0,0,0,0.18)"; }}
                        onBlur={(e) => { e.currentTarget.style.borderColor = "var(--border-subtle)"; e.currentTarget.style.boxShadow = "none"; }}
                      >
                        {/* Header */}
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                          <ProviderLogo provider={m.provider} size={32} />
                          <div>
                            <code style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                              fontSize: "0.8125rem", fontWeight: 600, color: "var(--text-primary)",
                              letterSpacing: "-0.01em",
                            }}>{m.model}</code>
                            <div style={{ display: "flex", alignItems: "center", gap: "0.3125rem", marginTop: "0.125rem" }}>
                              <span style={{ width: 7, height: 7, borderRadius: "50%", background: brand(m.provider) }} />
                              <span style={{ fontSize: "0.6875rem", fontWeight: 500, color: "var(--text-tertiary)", textTransform: "capitalize" }}>{m.provider}</span>
                            </div>
                          </div>
                        </div>
                        {/* Context */}
                        {meta?.ctx && <ContextBar ctx={meta.ctx} ctxK={meta.ctxK} />}
                        {/* Caps */}
                        {meta?.caps?.length ? (
                          <div style={{ display: "flex", gap: "0.1875rem", flexWrap: "wrap" }}>
                            {meta.caps.map((c) => <CapTag key={c} cap={c} />)}
                          </div>
                        ) : null}
                        {/* Footer badges */}
                        <div style={{ display: "flex", gap: "0.25rem", marginTop: "auto", flexWrap: "wrap" }}>
                          {m.supports_streaming && (
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: "0.1875rem",
                              padding: "0.0625rem 0.375rem", fontSize: "0.625rem", fontWeight: 600,
                              color: "#10a37f", background: "rgba(16,163,127,0.07)", borderRadius: 4,
                            }}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                              stream
                            </span>
                          )}
                          <TierBadge value={m.cost_tier} />
                          <TierBadge value={m.latency_tier} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                {filtered.length > PAGE_SIZE && (
                  <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
